-- ============================================================
-- Incremental loading: run each section one at a time,
-- measure query performance after each load.
--
-- Usage: cd data/src/resale/incremental
--        psql -U postgres -d sd6103_project
--        Then paste each section below one at a time.
-- ============================================================

-- ============ RESET: clear existing resale data ============
-- Run this ONCE before starting the incremental loading
TRUNCATE resale_transaction CASCADE;
TRUNCATE hdb_flat CASCADE;

-- ============ LOAD FILE 1 (2015-2016, ~37K rows) ============
\copy hdb_flat(flat_id, block_id, flat_type, storey_min, storey_max, floor_area_sqm, flat_model) FROM 'file1_hdb_flat.csv' WITH (FORMAT csv, HEADER true);
\copy resale_transaction(transaction_id, flat_id, transaction_year, transaction_month, remaining_lease_years, resale_price) FROM 'file1_resale_transaction.csv' WITH (FORMAT csv, HEADER true);
SELECT 'After File 1' AS stage, COUNT(*) AS txn_count FROM resale_transaction;
-- >>> NOW RUN YOUR QUERIES Q1-Q6, EACH 5 TIMES, RECORD TIMING <<<

-- ============ LOAD FILE 2 (2012-2014, +52K rows) ============
\copy hdb_flat(flat_id, block_id, flat_type, storey_min, storey_max, floor_area_sqm, flat_model) FROM 'file2_hdb_flat.csv' WITH (FORMAT csv, HEADER true);
\copy resale_transaction(transaction_id, flat_id, transaction_year, transaction_month, remaining_lease_years, resale_price) FROM 'file2_resale_transaction.csv' WITH (FORMAT csv, HEADER true);
SELECT 'After File 2' AS stage, COUNT(*) AS txn_count FROM resale_transaction;
-- >>> NOW RUN YOUR QUERIES Q1-Q6, EACH 5 TIMES, RECORD TIMING <<<

-- ============ LOAD FILE 3 (1990-1999, +278K rows) ============
\copy hdb_flat(flat_id, block_id, flat_type, storey_min, storey_max, floor_area_sqm, flat_model) FROM 'file3_hdb_flat.csv' WITH (FORMAT csv, HEADER true);
\copy resale_transaction(transaction_id, flat_id, transaction_year, transaction_month, remaining_lease_years, resale_price) FROM 'file3_resale_transaction.csv' WITH (FORMAT csv, HEADER true);
SELECT 'After File 3' AS stage, COUNT(*) AS txn_count FROM resale_transaction;
-- >>> NOW RUN YOUR QUERIES Q1-Q6, EACH 5 TIMES, RECORD TIMING <<<

-- ============ LOAD FILE 4 (2000-2012, +366K rows) ============
\copy hdb_flat(flat_id, block_id, flat_type, storey_min, storey_max, floor_area_sqm, flat_model) FROM 'file4_hdb_flat.csv' WITH (FORMAT csv, HEADER true);
\copy resale_transaction(transaction_id, flat_id, transaction_year, transaction_month, remaining_lease_years, resale_price) FROM 'file4_resale_transaction.csv' WITH (FORMAT csv, HEADER true);
SELECT 'After File 4' AS stage, COUNT(*) AS txn_count FROM resale_transaction;
-- >>> NOW RUN YOUR QUERIES Q1-Q6, EACH 5 TIMES, RECORD TIMING <<<

-- ============ LOAD FILE 5 (2017-onwards, +243K rows) ============
\copy hdb_flat(flat_id, block_id, flat_type, storey_min, storey_max, floor_area_sqm, flat_model) FROM 'file5_hdb_flat.csv' WITH (FORMAT csv, HEADER true);
\copy resale_transaction(transaction_id, flat_id, transaction_year, transaction_month, remaining_lease_years, resale_price) FROM 'file5_resale_transaction.csv' WITH (FORMAT csv, HEADER true);
SELECT 'After File 5' AS stage, COUNT(*) AS txn_count FROM resale_transaction;
-- >>> NOW RUN YOUR QUERIES Q1-Q6, EACH 5 TIMES, RECORD TIMING <<<

-- Reset sequences after all loading
SELECT setval('hdb_flat_flat_id_seq', (SELECT MAX(flat_id) FROM hdb_flat));
SELECT setval('resale_transaction_transaction_id_seq', (SELECT MAX(transaction_id) FROM resale_transaction));
