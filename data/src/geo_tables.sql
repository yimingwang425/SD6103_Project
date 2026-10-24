-- ============================================================
-- SD6103 Project: Hawker Centre, Proximity Level & Proximity tables
-- Run: cd data/src && psql -U postgres -d sd6103_project -f geo_tables.sql
-- ============================================================

DROP TABLE IF EXISTS proximity CASCADE;
DROP TABLE IF EXISTS proximity_level CASCADE;
DROP TABLE IF EXISTS hawker_centre CASCADE;

CREATE TABLE hawker_centre (
    hawker_id       SERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,
    address         VARCHAR(200),
    latitude        NUMERIC(15,8) NOT NULL,
    longitude       NUMERIC(15,8) NOT NULL,
    has_fresh_market BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE proximity_level (
    level_id        INTEGER PRIMARY KEY,
    category        VARCHAR(10) NOT NULL,
    distance_min    NUMERIC(8,1) NOT NULL,
    distance_max    NUMERIC(8,1) NOT NULL
);

CREATE TABLE proximity (
    block_id        INTEGER NOT NULL REFERENCES hdb_block(block_id),
    hawker_id       INTEGER NOT NULL REFERENCES hawker_centre(hawker_id),
    distance_m      NUMERIC(10,2) NOT NULL,
    level_id        INTEGER NOT NULL REFERENCES proximity_level(level_id),
    PRIMARY KEY (block_id, hawker_id)
);

-- Load hawker centres (skip source_row column — need trimmed CSV)
-- We use a temp table to handle the extra source_row column
CREATE TEMP TABLE hawker_raw (
    hawker_id       INTEGER,
    source_row      INTEGER,
    name            VARCHAR(100),
    address         VARCHAR(200),
    latitude        NUMERIC(15,8),
    longitude       NUMERIC(15,8),
    has_fresh_market INTEGER
);
\copy hawker_raw FROM '../hawker_geo/hawker_centres_clean.csv' WITH (FORMAT csv, HEADER true);
INSERT INTO hawker_centre (hawker_id, name, address, latitude, longitude, has_fresh_market)
SELECT hawker_id, name, address, latitude, longitude, has_fresh_market::boolean FROM hawker_raw;
DROP TABLE hawker_raw;

-- Load proximity levels (static data)
INSERT INTO proximity_level (level_id, category, distance_min, distance_max) VALUES
    (1, 'NEAR',   0.0,    800.0),
    (2, 'MEDIUM', 800.1,  2000.0),
    (3, 'FAR',    2000.1, 99999.0);

-- Load proximity
\copy proximity(block_id, hawker_id, distance_m, level_id) FROM '../hawker_geo/proximity.csv' WITH (FORMAT csv, HEADER true);

-- Reset sequence
SELECT setval('hawker_centre_hawker_id_seq', (SELECT MAX(hawker_id) FROM hawker_centre));

-- Verify
SELECT 'hawker_centre' AS table_name, COUNT(*) AS row_count FROM hawker_centre
UNION ALL
SELECT 'proximity_level', COUNT(*) FROM proximity_level
UNION ALL
SELECT 'proximity', COUNT(*) FROM proximity;
