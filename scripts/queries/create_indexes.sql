-- Task 4 indexes. Run after the schema and the data load; each one is
-- justified in the report by the query plan it changes.

-- Q1 reads a three-year window of the resale transactions
CREATE INDEX IF NOT EXISTS resale_transaction_month_idx
    ON public.resale_transaction (transaction_year, transaction_month);

-- Q3 and Q4 look up transactions by flat; the included columns answer
-- both queries from the index without reading the table
CREATE INDEX IF NOT EXISTS resale_transaction_flat_idx
    ON public.resale_transaction (flat_id) INCLUDE (resale_price, transaction_year);
