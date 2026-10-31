-- Task 4 indexes. Run after the schema and the data load; each one is
-- justified in the report by the query plan it changes.

-- Q1 filters resale transactions by year, Q2/Q5 scan the whole table
CREATE INDEX IF NOT EXISTS resale_transaction_month_idx
    ON public.resale_transaction (transaction_year, transaction_month);

-- Q3 and Q4 resolve flat -> transaction per block, i.e. a foreign key lookup
CREATE INDEX IF NOT EXISTS resale_transaction_flat_idx
    ON public.resale_transaction (flat_id);

-- Q4 and Q6 group blocks by town through hdb_block
CREATE INDEX IF NOT EXISTS idx_hdb_block_town
    ON public.hdb_block (town_id);

-- Q4 and Q6 keep one proximity level of the 51,214 pairs
CREATE INDEX IF NOT EXISTS idx_prox_level
    ON public.proximity (level_id);
