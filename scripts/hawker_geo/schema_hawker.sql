-- schema_hawker.sql - the hawker-geo tables, aligned with the team's logical
-- schema (logical_schema.drawio). Load order: TOWN, HDB_BLOCK, HDB_FLAT,
-- HAWKER_CENTRE, PROXIMITY_LEVEL, PROXIMITY, RESALE_TRANSACTION.
-- Run from the repository root, then load the CSVs (see README).

-- 26 planning towns; ids match the team's hdb_town_YM.csv
CREATE TABLE TOWN (
    town_id   INT         PRIMARY KEY,
    town_name VARCHAR(50) NOT NULL UNIQUE
);

-- 12,442 blocks from the HDB property register; lease_commence_year = year
-- completed; postal stays VARCHAR so leading zeros survive
CREATE TABLE HDB_BLOCK (
    block_id            INT          PRIMARY KEY,
    town_id             INT          NOT NULL REFERENCES TOWN(town_id),
    block_number        VARCHAR(10)  NOT NULL,
    street              VARCHAR(200) NOT NULL,
    lease_commence_year INT          NOT NULL,
    latitude            NUMERIC(9,6) NOT NULL,
    longitude           NUMERIC(9,6) NOT NULL,
    postal              VARCHAR(8),
    UNIQUE (block_number, street)
);

-- a flat = (block, flat_type, storey_range, floor_area_sqm, flat_model): the
-- resale data carries no unit number, so identical units collapse into one row.
-- flat_model is VARCHAR(40), wider than the diagram's 20: the longest model in
-- the data, "Premium Apartment Loft", needs 22
CREATE TABLE HDB_FLAT (
    flat_id        INT          PRIMARY KEY,
    block_id       INT          NOT NULL REFERENCES HDB_BLOCK(block_id),
    flat_type      VARCHAR(20)  NOT NULL,
    storey_range   VARCHAR(20)  NOT NULL,
    floor_area_sqm NUMERIC(9,2) NOT NULL,
    flat_model     VARCHAR(40)  NOT NULL
);

-- 129 centres; stall counts from the official register, NULL for the 22
-- opened after its snapshot - unknown, not "no market"
CREATE TABLE HAWKER_CENTRE (
    hawker_id                INT          PRIMARY KEY,
    hawker_name              VARCHAR(100) NOT NULL UNIQUE,
    street                   VARCHAR(200),
    latitude                 NUMERIC(9,6) NOT NULL,
    longitude                NUMERIC(9,6) NOT NULL,
    postal                   VARCHAR(8),
    no_of_mkt_produce_stalls INT,
    no_of_cooked_food_stalls INT
);

-- walking bands (min, max]: a distance on a boundary belongs to the lower
-- level. Walking distances, not the straight-line 800/2000 m bands
CREATE TABLE PROXIMITY_LEVEL (
    level_id       INT          PRIMARY KEY,
    category       VARCHAR(10)  NOT NULL UNIQUE,
    distance_min_m NUMERIC(8,2),
    distance_max_m NUMERIC(8,2)
);

-- the M:N relation, OSRM foot routing; level_id is derived from distance_m
-- and CHECKed against it, so the table cannot drift from the bands
CREATE TABLE PROXIMITY (
    block_id   INT          NOT NULL REFERENCES HDB_BLOCK(block_id),
    hawker_id  INT          NOT NULL REFERENCES HAWKER_CENTRE(hawker_id),
    level_id   INT          NOT NULL REFERENCES PROXIMITY_LEVEL(level_id),
    distance_m NUMERIC(8,2) NOT NULL CHECK (distance_m > 0),
    PRIMARY KEY (block_id, hawker_id),
    CONSTRAINT proximity_level_matches_distance CHECK (
        (level_id = 1 AND distance_m <= 500)
        OR (level_id = 2 AND distance_m > 500 AND distance_m <= 1000)
        OR (level_id = 3 AND distance_m > 1000))
);

-- flat_id is NULL for the 12,938 transactions on blocks demolished before the
-- current register snapshot, so no flat - and no level - exists for them;
-- transaction_month is stored as the first of the month
CREATE TABLE RESALE_TRANSACTION (
    transaction_id        INT           PRIMARY KEY,
    flat_id               INT           REFERENCES HDB_FLAT(flat_id),
    transaction_month     DATE          NOT NULL,
    remaining_lease_years INT           NOT NULL,
    resale_price          NUMERIC(12,2) NOT NULL
);

CREATE INDEX resale_transaction_flat_idx  ON RESALE_TRANSACTION (flat_id);
CREATE INDEX resale_transaction_month_idx ON RESALE_TRANSACTION (transaction_month);

-- a block's own level = the level of the nearest centre it can walk to
-- (this is what Q4 joins on)
CREATE VIEW HDB_BLOCK_PROXIMITY_LEVEL AS
SELECT block_id,
       MIN(level_id)   AS level_id,
       MIN(distance_m) AS nearest_distance_m
FROM   PROXIMITY
GROUP  BY block_id;
