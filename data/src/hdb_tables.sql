-- ============================================================
-- SD6103 Project: Town & HDB Block tables
-- Run: cd data/src && psql -U postgres -d sd6103_project -f hdb_tables.sql
-- ============================================================

DROP TABLE IF EXISTS hdb_block CASCADE;
DROP TABLE IF EXISTS town CASCADE;

CREATE TABLE town (
    town_id     SERIAL PRIMARY KEY,
    town_name   VARCHAR(30) NOT NULL UNIQUE
);

CREATE TABLE hdb_block (
    block_id    SERIAL PRIMARY KEY,
    town_id     INTEGER NOT NULL REFERENCES town(town_id),
    blk_no      VARCHAR(10) NOT NULL,
    street      VARCHAR(50) NOT NULL,
    year_completed  INTEGER,
    latitude    NUMERIC(15,11),
    longitude   NUMERIC(15,11),
    postal      VARCHAR(10),
    UNIQUE (blk_no, street)
);

-- Load data
\copy town(town_id, town_name) FROM '../clean_data/hdb_town_YM.csv' WITH (FORMAT csv, HEADER true);
\copy hdb_block(block_id, town_id, blk_no, street, year_completed, latitude, longitude, postal) FROM '../clean_data/hdb_block_import.csv' WITH (FORMAT csv, HEADER true);

-- Reset sequences
SELECT setval('town_town_id_seq', (SELECT MAX(town_id) FROM town));
SELECT setval('hdb_block_block_id_seq', (SELECT MAX(block_id) FROM hdb_block));

-- Add FK from hdb_flat to hdb_block (if hdb_flat already exists)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'hdb_flat') THEN
        ALTER TABLE hdb_flat ADD CONSTRAINT fk_flat_block
            FOREIGN KEY (block_id) REFERENCES hdb_block(block_id);
        RAISE NOTICE 'FK hdb_flat -> hdb_block added';
    END IF;
END $$;

-- Verify
SELECT 'town' AS table_name, COUNT(*) AS row_count FROM town
UNION ALL
SELECT 'hdb_block', COUNT(*) FROM hdb_block;
