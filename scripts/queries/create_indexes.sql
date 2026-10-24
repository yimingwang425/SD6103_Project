-- ============================================================
-- SD6103 Project - Task 4: Index Creation
--
-- Indexes chosen based on Q1-Q6 query patterns:
--   - Foreign key columns used in JOINs
--   - Columns used in WHERE filters
--
-- Usage:
--   psql -U postgres -d sd6103_project -f create_indexes.sql
-- ============================================================

-- Index 1: resale_transaction.flat_id
-- Reason: Every query joins resale_transaction → hdb_flat on flat_id.
--         Without this index, PostgreSQL does a sequential scan on the
--         largest table (~976K rows) for every join.
CREATE INDEX idx_resale_txn_flat_id
    ON resale_transaction(flat_id);

-- Index 2: hdb_flat.block_id
-- Reason: Every query joins hdb_flat → hdb_block on block_id.
CREATE INDEX idx_hdb_flat_block_id
    ON hdb_flat(block_id);

-- Index 3: hdb_block.town_id
-- Reason: Every query joins hdb_block → town on town_id.
CREATE INDEX idx_hdb_block_town_id
    ON hdb_block(town_id);

-- Index 4: hdb_flat.flat_type
-- Reason: Q1 filters WHERE flat_type IN ('4 ROOM', '5 ROOM'),
--         Q3 filters WHERE flat_type = '3 ROOM'.
CREATE INDEX idx_hdb_flat_flat_type
    ON hdb_flat(flat_type);

-- Index 5: resale_transaction.transaction_year
-- Reason: Q1 filters WHERE transaction_year BETWEEN 2015 AND 2017.
CREATE INDEX idx_resale_txn_year
    ON resale_transaction(transaction_year);

-- Index 6: hawker_proximity.proximity_class
-- Reason: Q4 filters WHERE proximity_class = 1 and proximity_class = 3.
CREATE INDEX idx_hawker_proximity_class
    ON hawker_proximity(proximity_class);

-- Index 7: hawker_proximity.hawker_id
-- Reason: Q5, Q6 join hawker_proximity → hawker_centre on hawker_id.
CREATE INDEX idx_hawker_proximity_hawker_id
    ON hawker_proximity(hawker_id);

-- Verify all indexes
SELECT tablename, indexname
FROM   pg_indexes
WHERE  schemaname = 'public'
  AND  indexname LIKE 'idx_%'
ORDER  BY tablename, indexname;
