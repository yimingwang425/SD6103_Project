-- ===========================================================================
-- schema_hawker.sql - hawker-geo workstream: the four tables this workstream
-- produces, plus the views the Q4/Q5 queries need.
--
-- Run from the REPOSITORY ROOT (the \copy paths are relative to it):
--   psql -U postgres -d sd6103_project -f scripts/hawker_geo/schema_hawker.sql
--
-- Load order is hdb_block -> hawker_centre -> hawker_proximity ->
-- resale_transaction; each later table references an earlier one. The queries
-- live in queries_hawker.sql.
-- ===========================================================================

DROP TABLE IF EXISTS resale_transaction CASCADE;
DROP TABLE IF EXISTS hawker_proximity CASCADE;
DROP TABLE IF EXISTS hawker_centre CASCADE;
DROP TABLE IF EXISTS hdb_block CASCADE;

-- One row per physical block, from siteselect_sg/hdb.csv. `postal_code` is NOT
-- unique in the source (14 codes shared by several blocks, 3 rows 'NIL'), so
-- the surrogate key block_id is required. `town` holds the 2-3 letter HDB town
-- code (AMK, CT, ...); the resale file spells the same 26 towns out in full -
-- both encodings are 1:1, see the town_name view below.
CREATE TABLE hdb_block (
    block_id    CHAR(6)      PRIMARY KEY,
    blk_no      VARCHAR(10)  NOT NULL,
    street      VARCHAR(80)  NOT NULL,
    postal_code VARCHAR(6),
    town        VARCHAR(20)  NOT NULL,
    latitude    NUMERIC(9,6) NOT NULL,
    longitude   NUMERIC(9,6)  NOT NULL,
    UNIQUE (blk_no, street)
);

-- 129 centres: the geo file is authoritative for locations, the official
-- data.gov.sg register for attributes. The 22 centres opened after the
-- register's snapshot have NULL stall counts - "unknown", not "no market".
CREATE TABLE hawker_centre (
    hawker_id                CHAR(4)       PRIMARY KEY,
    name                     VARCHAR(120)  NOT NULL UNIQUE,
    alias                    VARCHAR(120),
    address                  VARCHAR(200),
    postal_code              CHAR(6),
    latitude                 NUMERIC(9,6)  NOT NULL,
    longitude                NUMERIC(9,6)  NOT NULL,
    no_of_stalls             SMALLINT,
    no_of_cooked_food_stalls SMALLINT,
    no_of_mkt_produce_stalls SMALLINT,
    -- stall counts only exist when the centre was matched to the register
    CONSTRAINT hawker_stalls_all_or_nothing CHECK (
        (no_of_stalls IS NULL AND no_of_cooked_food_stalls IS NULL
                                 AND no_of_mkt_produce_stalls IS NULL)
        OR (no_of_stalls IS NOT NULL AND no_of_cooked_food_stalls IS NOT NULL
                                       AND no_of_mkt_produce_stalls IS NOT NULL))
);

-- The M:N relation: walking path length (OSRM foot routing, audited against
-- OneMap), not great-circle distance. 63,340 pairs, every block covered.
-- class 1: walk <= 500 m, class 2: <= 1000 m, class 3: > 1000 m.
CREATE TABLE hawker_proximity (
    block_id        CHAR(6)      NOT NULL REFERENCES hdb_block(block_id),
    hawker_id       CHAR(4)      NOT NULL REFERENCES hawker_centre(hawker_id),
    walk_distance_m NUMERIC(7,1) NOT NULL CHECK (walk_distance_m > 0),
    walk_duration_s NUMERIC(7,1)          CHECK (walk_duration_s >= 0),
    proximity_class SMALLINT     NOT NULL CHECK (proximity_class BETWEEN 1 AND 3),
    PRIMARY KEY (block_id, hawker_id),
    -- the class definition is part of the schema, so the table cannot drift
    -- away from the definition used in the report
    CONSTRAINT proximity_class_matches_distance CHECK (
        (proximity_class = 1 AND walk_distance_m <= 500)
        OR (proximity_class = 2 AND walk_distance_m > 500 AND walk_distance_m <= 1000)
        OR (proximity_class = 3 AND walk_distance_m > 1000))
);

-- The fact table Q4/Q5 aggregate over. block_id is NULLABLE on purpose: 12,938
-- of 986,720 transactions (1.31%) happened on blocks demolished before the
-- current HDB register snapshot, so no geocode - and no proximity class - can
-- exist for them. Keeping them with a NULL FK keeps counts truthful; an INNER
-- JOIN excludes them from Q4/Q5 automatically.
CREATE TABLE resale_transaction (
    resale_id             CHAR(8)       PRIMARY KEY,
    block_id              CHAR(6)       REFERENCES hdb_block(block_id),
    month                 CHAR(7)       NOT NULL
                          CHECK (month ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    town                  VARCHAR(20)   NOT NULL,
    flat_type             VARCHAR(20)   NOT NULL
                          CHECK (flat_type IN ('1 ROOM', '2 ROOM', '3 ROOM',
                                               '4 ROOM', '5 ROOM', 'EXECUTIVE',
                                               'MULTI-GENERATION')),
    storey_min            SMALLINT      NOT NULL CHECK (storey_min BETWEEN 1 AND 60),
    storey_max            SMALLINT      NOT NULL CHECK (storey_max BETWEEN 1 AND 60),
    floor_area_sqm        NUMERIC(6,1)  NOT NULL CHECK (floor_area_sqm > 0),
    flat_model            VARCHAR(40)   NOT NULL,
    lease_commence_date   SMALLINT      NOT NULL
                          CHECK (lease_commence_date BETWEEN 1960 AND 2030),
    remaining_lease_years SMALLINT      NOT NULL
                          CHECK (remaining_lease_years BETWEEN 0 AND 99),
    resale_price          INTEGER       NOT NULL CHECK (resale_price > 0),
    CONSTRAINT resale_storey_range_ordered CHECK (storey_min <= storey_max),
    -- a 99-year lease cannot outlive its term; one year of slack absorbs the
    -- source's "X years Y months" rounding (measured max excess: exactly 1)
    CONSTRAINT resale_lease_consistent CHECK (
        remaining_lease_years
        <= 100 - (CAST(SUBSTRING(month FROM 1 FOR 4) AS SMALLINT) - lease_commence_date))
);

CREATE INDEX resale_transaction_block_idx ON resale_transaction (block_id);
CREATE INDEX resale_transaction_month_idx ON resale_transaction (month);

-- A block's own proximity class = the class of the closest centre it can walk
-- to (this is what Q4 joins on).
CREATE VIEW hdb_block_proximity_class AS
SELECT block_id,
       MIN(proximity_class) AS proximity_class,
       MIN(walk_distance_m) AS nearest_walk_m
FROM   hawker_proximity
GROUP  BY block_id;

-- Town code -> full planning-town name, so hdb_block.town and
-- resale_transaction.town can be joined to the spelled-out form.
CREATE VIEW town_name (town_code, town_name) AS
VALUES ('AMK', 'ANG MO KIO'),      ('BB', 'BUKIT BATOK'),
       ('BD', 'BEDOK'),            ('BH', 'BISHAN'),
       ('BM', 'BUKIT MERAH'),      ('BP', 'BUKIT PANJANG'),
       ('BT', 'BUKIT TIMAH'),      ('CCK', 'CHOA CHU KANG'),
       ('CL', 'CLEMENTI'),         ('CT', 'CENTRAL AREA'),
       ('GL', 'GEYLANG'),          ('HG', 'HOUGANG'),
       ('JE', 'JURONG EAST'),      ('JW', 'JURONG WEST'),
       ('KWN', 'KALLANG/WHAMPOA'), ('MP', 'MARINE PARADE'),
       ('PG', 'PUNGGOL'),          ('PRC', 'PASIR RIS'),
       ('QT', 'QUEENSTOWN'),       ('SB', 'SEMBAWANG'),
       ('SGN', 'SERANGOON'),       ('SK', 'SENGKANG'),
       ('TAP', 'TAMPINES'),        ('TP', 'TOA PAYOH'),
       ('WL', 'WOODLANDS'),        ('YS', 'YISHUN');

-- Two CSVs carry provenance columns their target table does not need, and
-- \copy maps file fields positionally onto the named columns - so those two
-- land in all-TEXT staging tables first and are then projected.
CREATE UNLOGGED TABLE stg_hawker_centre (
    hawker_id TEXT, name TEXT, alias TEXT, address TEXT, postal_code TEXT,
    latitude TEXT, longitude TEXT, type_of_centre TEXT, owner TEXT,
    no_of_stalls TEXT, no_of_cooked_food_stalls TEXT,
    no_of_mkt_produce_stalls TEXT, has_fresh_market TEXT, official_id TEXT,
    match_method TEXT
);

CREATE UNLOGGED TABLE stg_hawker_proximity (
    block_id TEXT, hawker_id TEXT, walk_distance_m TEXT, walk_duration_s TEXT,
    proximity_class TEXT, proximity_label TEXT, hawker_has_fresh_market TEXT
);

\copy hdb_block(block_id, blk_no, street, postal_code, town, latitude, longitude) FROM 'data/hawker_geo/hdb_block.csv' WITH (FORMAT csv, HEADER true, NULL '')
\copy stg_hawker_centre FROM 'data/hawker_geo/hawker_centre.csv' WITH (FORMAT csv, HEADER true, NULL '')
\copy stg_hawker_proximity FROM 'data/hawker_geo/hdb_hawker_proximity.csv' WITH (FORMAT csv, HEADER true, NULL '')
\copy resale_transaction(resale_id, block_id, month, town, flat_type, storey_min, storey_max, floor_area_sqm, flat_model, lease_commence_date, remaining_lease_years, resale_price) FROM 'data/hawker_geo/resale_transaction.csv' WITH (FORMAT csv, HEADER true, NULL '')

INSERT INTO hawker_centre (
    hawker_id, name, alias, address, postal_code, latitude, longitude,
    no_of_stalls, no_of_cooked_food_stalls, no_of_mkt_produce_stalls
)
SELECT hawker_id, name, NULLIF(alias, ''), NULLIF(address, ''),
       NULLIF(postal_code, ''), latitude::NUMERIC(9,6), longitude::NUMERIC(9,6),
       NULLIF(no_of_stalls, '')::SMALLINT,
       NULLIF(no_of_cooked_food_stalls, '')::SMALLINT,
       NULLIF(no_of_mkt_produce_stalls, '')::SMALLINT
FROM   stg_hawker_centre;

INSERT INTO hawker_proximity (block_id, hawker_id, walk_distance_m, walk_duration_s, proximity_class)
SELECT block_id, hawker_id, walk_distance_m::NUMERIC(7,1),
       NULLIF(walk_duration_s, '')::NUMERIC(7,1), proximity_class::SMALLINT
FROM   stg_hawker_proximity;

DROP TABLE stg_hawker_centre;
DROP TABLE stg_hawker_proximity;

-- Verify: must be 12,442 / 129 / 63,340 / 986,720
SELECT 'hdb_block' AS table_name, COUNT(*) AS rows FROM hdb_block
UNION ALL SELECT 'hawker_centre', COUNT(*) FROM hawker_centre
UNION ALL SELECT 'hawker_proximity', COUNT(*) FROM hawker_proximity
UNION ALL SELECT 'resale_transaction', COUNT(*) FROM resale_transaction;
