-- ============================================================
-- SD6103 Project: Create HDB Flat & Resale Transaction tables
--
-- Prerequisites:
--   - Database sd6103_project must exist
--   - Tables town and hdb_block must already be created and loaded
--
-- Usage:
--   psql -U postgres -d sd6103_project -f scripts/resale/03_create_tables.sql
-- ============================================================

-- Drop existing tables (if any)
DROP TABLE IF EXISTS resale_transaction CASCADE;
DROP TABLE IF EXISTS hdb_flat CASCADE;

-- HDB Flat table
-- Each row = unique flat type within a storey band in a block.
-- Identity: (block_id, flat_type, storey_min, storey_max, floor_area_sqm, flat_model)
-- enforced via UNIQUE constraint; used by 04_incremental_load's ON CONFLICT DO NOTHING.
CREATE TABLE hdb_flat (
    flat_id         INT GENERATED ALWAYS AS IDENTITY,
    block_id        INTEGER NOT NULL REFERENCES hdb_block(block_id),
    flat_type       VARCHAR(20) NOT NULL,
    storey_min      INTEGER NOT NULL,
    storey_max      INTEGER NOT NULL,
    floor_area_sqm  NUMERIC(6,1) NOT NULL,
    flat_model      VARCHAR(30) NOT NULL,

    CONSTRAINT pk_hdb_flat PRIMARY KEY (flat_id),
    CONSTRAINT uq_flat_identity UNIQUE (block_id, flat_type, storey_min, storey_max, floor_area_sqm, flat_model)
);

-- Resale Transaction table
CREATE TABLE resale_transaction (
    transaction_id          INT GENERATED ALWAYS AS IDENTITY,
    flat_id                 INTEGER NOT NULL REFERENCES hdb_flat(flat_id),
    transaction_year        INTEGER NOT NULL,
    transaction_month       INTEGER NOT NULL CHECK (transaction_month BETWEEN 1 AND 12),
    remaining_lease_years   INTEGER NOT NULL CHECK (remaining_lease_years BETWEEN 0 AND 99),
    resale_price            INTEGER NOT NULL CHECK (resale_price > 0),

    CONSTRAINT pk_resale_transaction PRIMARY KEY (transaction_id)
);

-- Verify
SELECT 'hdb_flat' AS table_name, COUNT(*) AS row_count FROM hdb_flat
UNION ALL
SELECT 'resale_transaction', COUNT(*) FROM resale_transaction;
