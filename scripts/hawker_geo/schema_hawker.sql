-- schema_hawker.sql - the four hawker-geo tables plus the view Q4 joins on.
-- Load order: hdb_block -> hawker_centre -> hawker_proximity -> resale_transaction.
-- Run from the repository root, then load the CSVs (see README).

-- one row per HDB block; block_id is a surrogate key (postal_code is not unique).
-- town holds the HDB code (AMK, CT, ...).
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

-- 129 hawker centres; stall counts from the official register, NULL for the 22
-- centres opened after its snapshot - unknown, not "no market".
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
    CONSTRAINT hawker_stalls_all_or_nothing CHECK (
        (no_of_stalls IS NULL AND no_of_cooked_food_stalls IS NULL
                              AND no_of_mkt_produce_stalls IS NULL)
        OR (no_of_stalls IS NOT NULL AND no_of_cooked_food_stalls IS NOT NULL
                                      AND no_of_mkt_produce_stalls IS NOT NULL))
);

-- the M:N relation: walking path length (OSRM foot routing), not straight-line
-- distance. class 1: <= 500 m, class 2: <= 1000 m, class 3: > 1000 m.
CREATE TABLE hawker_proximity (
    block_id        CHAR(6)      NOT NULL REFERENCES hdb_block(block_id),
    hawker_id       CHAR(4)      NOT NULL REFERENCES hawker_centre(hawker_id),
    walk_distance_m NUMERIC(7,1) NOT NULL CHECK (walk_distance_m > 0),
    walk_duration_s NUMERIC(7,1)          CHECK (walk_duration_s >= 0),
    proximity_class SMALLINT     NOT NULL CHECK (proximity_class BETWEEN 1 AND 3),
    PRIMARY KEY (block_id, hawker_id),
    CONSTRAINT proximity_class_matches_distance CHECK (
        (proximity_class = 1 AND walk_distance_m <= 500)
        OR (proximity_class = 2 AND walk_distance_m > 500 AND walk_distance_m <= 1000)
        OR (proximity_class = 3 AND walk_distance_m > 1000))
);

-- the fact table Q4/Q5 aggregate over. block_id is nullable: 12,938 transactions
-- sit on blocks demolished before the current register snapshot, so no proximity
-- class can exist for them. town holds the full planning-town name here.
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
    CONSTRAINT resale_lease_consistent CHECK (
        remaining_lease_years
        <= 100 - (CAST(SUBSTRING(month FROM 1 FOR 4) AS SMALLINT) - lease_commence_date))
);

CREATE INDEX resale_transaction_block_idx ON resale_transaction (block_id);
CREATE INDEX resale_transaction_month_idx ON resale_transaction (month);

-- a block's own proximity class = the class of the nearest centre it can walk
-- to (this is what Q4 joins on)
CREATE VIEW hdb_block_proximity_class AS
SELECT block_id,
       MIN(proximity_class) AS proximity_class,
       MIN(walk_distance_m) AS nearest_walk_m
FROM   hawker_proximity
GROUP  BY block_id;
