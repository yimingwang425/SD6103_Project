-- ============================================================
-- SD6103 Project: Create HDB Flat & Resale Transaction tables
--
-- Prerequisites:
--   - Database sd6103_project must exist
--   - Tables town and hdb_block must already be created and loaded
--
-- Usage:
--   psql -U postgres -d sd6103_project -f 03_create_tables.sql
-- ============================================================

-- Drop existing tables (if any)
DROP TABLE IF EXISTS resale_transaction CASCADE;
DROP TABLE IF EXISTS hdb_flat CASCADE;

-- HDB Flat table
-- Each row represents a unique flat type within a storey band in a block.
-- A flat is defined by the combination of:
--   (block_id, flat_type, storey_min, storey_max, floor_area_sqm, flat_model)
-- This is the finest granularity available in the raw data (no unit-level ID).
CREATE TABLE hdb_flat (
    flat_id         SERIAL PRIMARY KEY,
    block_id        INTEGER NOT NULL REFERENCES hdb_block(block_id),
    flat_type       VARCHAR(20) NOT NULL,
    storey_min      INTEGER NOT NULL,
    storey_max      INTEGER NOT NULL,
    floor_area_sqm  NUMERIC(6,1) NOT NULL,
    flat_model      VARCHAR(30) NOT NULL
);

-- Resale Transaction table
-- Each row represents one resale transaction record.
CREATE TABLE resale_transaction (
    transaction_id          SERIAL PRIMARY KEY,
    flat_id                 INTEGER NOT NULL REFERENCES hdb_flat(flat_id),
    transaction_year        INTEGER NOT NULL,
    transaction_month       INTEGER NOT NULL CHECK (transaction_month BETWEEN 1 AND 12),
    remaining_lease_years   INTEGER NOT NULL CHECK (remaining_lease_years BETWEEN 0 AND 99),
    resale_price            INTEGER NOT NULL CHECK (resale_price > 0)
);

-- Verify
SELECT 'hdb_flat' AS table_name, COUNT(*) AS row_count FROM hdb_flat
UNION ALL
SELECT 'resale_transaction', COUNT(*) FROM resale_transaction;
