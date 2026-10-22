-- ===========================================================================
-- 04_schema_hawker.sql
-- Data Systems Project - suggested DDL for the hawker-geo workstream.
--
-- This is a *fragment* to be merged into the team's schema. It covers the two
-- tables this workstream produces, plus the hdb_block table they depend on.
-- Load order matters: hdb_block and hawker_centre first, then the relation.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- HDB block (one row per physical block; the parent of HDB Flat)
-- Sourced from siteselect_sg/hdb.csv. `postal_code` is deliberately NOT unique:
-- 14 postal codes are shared by more than one block in the source file, and 3
-- rows have the literal string 'NIL', so the surrogate key is required.
-- ---------------------------------------------------------------------------
CREATE TABLE hdb_block (
    block_id    CHAR(6)      PRIMARY KEY,
    blk_no      VARCHAR(10)  NOT NULL,
    street      VARCHAR(80)  NOT NULL,
    postal_code VARCHAR(6),
    town        VARCHAR(20)  NOT NULL,
    latitude    NUMERIC(9,6) NOT NULL CHECK (latitude  BETWEEN 1.130 AND 1.500),
    longitude   NUMERIC(9,6) NOT NULL CHECK (longitude BETWEEN 103.550 AND 104.150),
    UNIQUE (blk_no, street)
);

-- ---------------------------------------------------------------------------
-- Hawker centre
--
-- 129 rows: the geo file is authoritative for locations. 106 of them carry
-- attributes from the official data.gov.sg register; the other 23 are hawker
-- centres opened after that register's snapshot, so their stall columns are
-- NULL rather than 0 - "unknown" and "no fresh market" are different facts,
-- and Query Q5 must not silently treat the former as the latter.
-- ---------------------------------------------------------------------------
CREATE TABLE hawker_centre (
    hawker_id               CHAR(4)      PRIMARY KEY,
    name                    VARCHAR(120) NOT NULL,
    alias                   VARCHAR(120),            -- colloquial name, if any
    address                 VARCHAR(200),            -- NULL when the source had none
    postal_code             CHAR(6)      CHECK (postal_code ~ '^[0-9]{6}$'),
    latitude                NUMERIC(9,6) NOT NULL
                            CHECK (latitude  BETWEEN 1.130 AND 1.500),
    longitude               NUMERIC(9,6) NOT NULL
                            CHECK (longitude BETWEEN 103.550 AND 104.150),
    type_of_centre          VARCHAR(3)
                            CHECK (type_of_centre IN ('HC', 'MHC', 'MK')),
    owner                   VARCHAR(32),
    no_of_stalls            SMALLINT     CHECK (no_of_stalls            >= 0),
    no_of_cooked_food_stalls SMALLINT    CHECK (no_of_cooked_food_stalls >= 0),
    no_of_mkt_produce_stalls SMALLINT    CHECK (no_of_mkt_produce_stalls >= 0),

    -- A stall is either a cooked-food stall or a market-produce stall, so the
    -- two sub-counts must add up to the total. Verified on all 106 rows.
    CONSTRAINT hawker_stalls_add_up CHECK (
        no_of_cooked_food_stalls + no_of_mkt_produce_stalls = no_of_stalls
    ),
    -- Stall counts only exist when the centre was matched to the register.
    CONSTRAINT hawker_stalls_all_or_nothing CHECK (
        (no_of_stalls IS NULL
         AND no_of_cooked_food_stalls IS NULL
         AND no_of_mkt_produce_stalls IS NULL)
        OR
        (no_of_stalls IS NOT NULL
         AND no_of_cooked_food_stalls IS NOT NULL
         AND no_of_mkt_produce_stalls IS NOT NULL)
    )
);

-- Convenience view for Q5: "a hawker with fresh market stalls".
CREATE VIEW hawker_with_fresh_market AS
SELECT hawker_id, name
FROM   hawker_centre
WHERE  no_of_mkt_produce_stalls > 0;

-- ---------------------------------------------------------------------------
-- Proximity: the M:N relation between HDB blocks and hawker centres.
--
-- Distance is *walking path length* from an OpenStreetMap foot-routing
-- service, not great-circle distance. Only pairs within 3000 m of walking are
-- materialised - and that set is provably complete, because the routing was
-- prefiltered at 3000 m great-circle and network distance >= great-circle
-- distance. Every block has at least one row (103 blocks in the outlying new
-- towns reach their nearest centre at 3000-3506 m and are kept by exception).
--
-- Proximity definition (3 levels, Task 1.1):
--   class 1 "very_near" : walk <=  500 m
--   class 2 "near"      : 500 < walk <= 1000 m
--   class 3 "far"       : walk > 1000 m
--
-- The CHECK constraint below makes the class definition part of the schema, so
-- the table cannot drift away from the definition used in the report.
-- ---------------------------------------------------------------------------
CREATE TABLE hawker_proximity (
    block_id         CHAR(6)      NOT NULL REFERENCES hdb_block(block_id),
    hawker_id        CHAR(4)      NOT NULL REFERENCES hawker_centre(hawker_id),
    walk_distance_m  NUMERIC(7,1) NOT NULL CHECK (walk_distance_m > 0),
    walk_duration_s  NUMERIC(7,1)          CHECK (walk_duration_s > 0),
    proximity_class  SMALLINT     NOT NULL CHECK (proximity_class BETWEEN 1 AND 3),

    PRIMARY KEY (block_id, hawker_id),

    CONSTRAINT proximity_class_matches_distance CHECK (
        (proximity_class = 1 AND walk_distance_m <= 500)
        OR (proximity_class = 2 AND walk_distance_m >  500
                                  AND walk_distance_m <= 1000)
        OR (proximity_class = 3 AND walk_distance_m > 1000)
    )
);

-- ---------------------------------------------------------------------------
-- Resale transaction (the fact table Q4/Q5 aggregate over)
--
-- The raw resale files identify a flat only by `block` + `street_name` text,
-- so the FK to hdb_block is the whole point of table 08. `block_id` is
-- deliberately NULLABLE: 12,938 of 986,720 transactions (1.31%, on 172 blocks)
-- happened on blocks that were demolished before the current HDB property
-- register snapshot, so no geocode - and therefore no proximity class - can
-- exist for them. Storing them with a NULL FK keeps transaction counts
-- truthful while an INNER JOIN quietly excludes them from Q4/Q5.
--
-- There is no natural key in the source (no transaction id), and 1,929
-- all-column duplicates are kept on purpose: two identical flats in the same
-- block can genuinely sell for the same price in the same month. Hence the
-- surrogate resale_id.
-- ---------------------------------------------------------------------------
CREATE TABLE resale_transaction (
    resale_id            CHAR(8)       PRIMARY KEY,
    block_id             CHAR(6)       REFERENCES hdb_block(block_id),
    month                CHAR(7)       NOT NULL
                         CHECK (month ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    town                 VARCHAR(20)   NOT NULL,
    flat_type            VARCHAR(20)   NOT NULL
                         CHECK (flat_type IN ('1 ROOM', '2 ROOM', '3 ROOM',
                                              '4 ROOM', '5 ROOM', 'EXECUTIVE',
                                              'MULTI-GENERATION')),
    storey_min           SMALLINT      NOT NULL CHECK (storey_min BETWEEN 1 AND 60),
    storey_max           SMALLINT      NOT NULL CHECK (storey_max BETWEEN 1 AND 60),
    floor_area_sqm       NUMERIC(6,1)  NOT NULL CHECK (floor_area_sqm > 0),
    flat_model           VARCHAR(40)   NOT NULL,
    lease_commence_date  SMALLINT      NOT NULL
                         CHECK (lease_commence_date BETWEEN 1960 AND 2030),
    remaining_lease_years SMALLINT     NOT NULL
                         CHECK (remaining_lease_years BETWEEN 0 AND 99),
    resale_price         INTEGER       NOT NULL CHECK (resale_price > 0),

    CONSTRAINT resale_storey_range_ordered CHECK (storey_min <= storey_max),
    -- A lease cannot outlive the 99-year term measured from its commencement.
    -- The bound allows ONE year of slack because the source states remaining
    -- lease as "X years Y months" and the cleaning keeps only the whole years,
    -- so a transaction dated January 2015 on a 1984 lease is legitimately
    -- recorded as 69 rather than 68. Measured over all 986,720 rows the
    -- maximum excess is exactly 1 year and only 1,207 rows sit on it, so this
    -- still rejects any gross inconsistency while accepting the source's own
    -- rounding. Verified before load; do not tighten without re-checking.
    CONSTRAINT resale_lease_consistent CHECK (
        remaining_lease_years
        <= 100 - (CAST(SUBSTRING(month FROM 1 FOR 4) AS SMALLINT) - lease_commence_date)
    )
);

CREATE INDEX resale_transaction_block_idx ON resale_transaction (block_id);
CREATE INDEX resale_transaction_month_idx ON resale_transaction (month);

-- ---------------------------------------------------------------------------
-- Bulk load (psql, run from the REPOSITORY ROOT, not from this folder). Load
-- order matters:
-- hdb_block -> hawker_centre -> hawker_proximity -> resale_transaction,
-- because each later table has a foreign key to an earlier one.
--
-- Two of the CSVs carry MORE columns than their target table: hawker_centre
-- also ships has_fresh_market / official_id / match_method (provenance), and
-- hdb_hawker_proximity also ships proximity_label / hawker_has_fresh_market
-- (denormalised for convenience). COPY maps file fields positionally onto the
-- named columns and errors on any extra field, so each of those two goes
-- through an all-TEXT staging table first and is then projected into the typed
-- table. This keeps the exported CSVs self-describing without forcing derived
-- columns into the 3NF schema.
-- ---------------------------------------------------------------------------
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
    type_of_centre, owner, no_of_stalls, no_of_cooked_food_stalls,
    no_of_mkt_produce_stalls
)
SELECT hawker_id,
       name,
       NULLIF(alias, ''),
       NULLIF(address, ''),
       NULLIF(postal_code, ''),
       latitude::NUMERIC(9,6),
       longitude::NUMERIC(9,6),
       NULLIF(type_of_centre, ''),
       NULLIF(owner, ''),
       NULLIF(no_of_stalls, '')::SMALLINT,
       NULLIF(no_of_cooked_food_stalls, '')::SMALLINT,
       NULLIF(no_of_mkt_produce_stalls, '')::SMALLINT
FROM   stg_hawker_centre;

INSERT INTO hawker_proximity (
    block_id, hawker_id, walk_distance_m, walk_duration_s, proximity_class
)
SELECT block_id,
       hawker_id,
       walk_distance_m::NUMERIC(7,1),
       NULLIF(walk_duration_s, '')::NUMERIC(7,1),
       proximity_class::SMALLINT
FROM   stg_hawker_proximity;

DROP TABLE stg_hawker_centre;
DROP TABLE stg_hawker_proximity;

-- Sanity check: these four counts must match the four CSV row counts
-- (12,442 / 129 / 63,340 / 986,720).
SELECT 'hdb_block' AS table_name, COUNT(*) FROM hdb_block
UNION ALL SELECT 'hawker_centre', COUNT(*) FROM hawker_centre
UNION ALL SELECT 'hawker_proximity', COUNT(*) FROM hawker_proximity
UNION ALL SELECT 'resale_transaction', COUNT(*) FROM resale_transaction;

-- ---------------------------------------------------------------------------
-- Q4 / Q5 support: a block's own proximity class is the class of the closest
-- hawker centre it can walk to. Materialise it once instead of recomputing the
-- MIN() in every query.
-- ---------------------------------------------------------------------------
CREATE VIEW hdb_block_proximity_class AS
SELECT p.block_id,
       MIN(p.proximity_class) AS proximity_class,
       MIN(p.walk_distance_m) AS nearest_walk_m
FROM   hawker_proximity p
GROUP  BY p.block_id;

-- ---------------------------------------------------------------------------
-- Q4 - median resale price per proximity class, within town.
--
-- Two deliberate restrictions:
--   * one flat type (4 ROOM) and one period (2020 onward), because price
--     differences between classes would otherwise be driven by flat size and
--     lease age rather than by location;
--   * HAVING n >= 30 keeps a town only when every class has enough
--     transactions for a median to mean anything. This is what reduces the
--     result from 26 towns to 22 - not missing data. The four that drop out
--     (BT, MP have no class-3 flats; PG, SGN have no class-1 flats) are a
--     geographic fact, and are worth stating explicitly in the report.
-- ---------------------------------------------------------------------------
WITH priced AS (
    SELECT b.town,
           c.proximity_class,
           t.resale_price
    FROM   resale_transaction t
    JOIN   hdb_block_proximity_class c ON c.block_id = t.block_id
    JOIN   hdb_block b                 ON b.block_id = t.block_id
    WHERE  t.flat_type = '4 ROOM'
      AND  t.month >= '2020-01'
)
SELECT town,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY resale_price)
             FILTER (WHERE proximity_class = 1)) AS median_class1,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY resale_price)
             FILTER (WHERE proximity_class = 2)) AS median_class2,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY resale_price)
             FILTER (WHERE proximity_class = 3)) AS median_class3,
       COUNT(*) FILTER (WHERE proximity_class = 1) AS n1,
       COUNT(*) FILTER (WHERE proximity_class = 2) AS n2,
       COUNT(*) FILTER (WHERE proximity_class = 3) AS n3
FROM   priced
GROUP  BY town
HAVING COUNT(*) FILTER (WHERE proximity_class = 1) >= 30
   AND COUNT(*) FILTER (WHERE proximity_class = 2) >= 30
   AND COUNT(*) FILTER (WHERE proximity_class = 3) >= 30
ORDER  BY median_class1 DESC;

-- ---------------------------------------------------------------------------
-- Q5 - does a fresh market within a 1 km walk associate with price?
--
-- `no_of_mkt_produce_stalls > 0` is the authoritative definition of "has a
-- fresh market": it comes from the official stall register, not from whether
-- the name happens to contain the word "market" (only 20 of 107 do). The 23
-- post-2016 centres with NULL stall data are therefore excluded - the STRICT
-- scenario. The sensitivity variant, which treats them as having a market,
-- simply swaps the predicate for:
--     (h.no_of_mkt_produce_stalls > 0 OR h.no_of_mkt_produce_stalls IS NULL)
-- ---------------------------------------------------------------------------
WITH block_fresh AS (
    SELECT b.block_id,
           EXISTS (
               SELECT 1
               FROM   hawker_proximity p
               JOIN   hawker_centre    h ON h.hawker_id = p.hawker_id
               WHERE  p.block_id = b.block_id
                 AND  p.walk_distance_m <= 1000
                 AND  h.no_of_mkt_produce_stalls > 0
           ) AS has_fresh_market_nearby
    FROM   hdb_block b
)
SELECT f.has_fresh_market_nearby,
       COUNT(*) AS n_txn,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY t.resale_price)) AS median_price,
       ROUND(AVG(t.resale_price))                                         AS mean_price
FROM   resale_transaction t
JOIN   block_fresh f ON f.block_id = t.block_id
WHERE  t.flat_type = '4 ROOM'
  AND  t.month >= '2020-01'
GROUP  BY f.has_fresh_market_nearby;
