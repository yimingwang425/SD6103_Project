-- Drop all custom indexes (for resetting before benchmark)
DROP INDEX IF EXISTS idx_resale_txn_flat_id;
DROP INDEX IF EXISTS idx_hdb_flat_block_id;
DROP INDEX IF EXISTS idx_hdb_block_town_id;
DROP INDEX IF EXISTS idx_hdb_flat_flat_type;
DROP INDEX IF EXISTS idx_resale_txn_year;
DROP INDEX IF EXISTS idx_hawker_proximity_class;
DROP INDEX IF EXISTS idx_hawker_proximity_hawker_id;
