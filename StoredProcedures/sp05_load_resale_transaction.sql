-- ============================================================
-- sp05_load_resale_transaction.sql
-- Stored Procedure: public.sp_load_resale_transaction()
--
-- Purpose:
--     Load resale transaction data from staging.resale into
--     public.resale_transaction. Resolves flat_id by joining
--     through two tables:
--       staging.resale -> hdb_block (on blk_no, street)
--                      -> hdb_flat  (on 6-field identity match)
--
-- Source table:
--     staging.resale (all TEXT columns, loaded by run_load.py)
--     Required columns: blk_no, street, flat_type, storey_min,
--                       storey_max, floor_area_sqm, flat_model,
--                       transaction_year, transaction_month,
--                       remaining_lease_years, resale_price
--
-- Target table:
--     public.resale_transaction (flat_id, transaction_year,
--         transaction_month, remaining_lease_years, resale_price)
--
-- Join chain:
--     1. staging.resale -> public.hdb_block
--        ON (blk_no = block_number AND street = street)
--        Resolves block_id from block address.
--
--     2. public.hdb_block -> public.hdb_flat
--        ON (block_id, flat_type, storey_min, storey_max,
--            floor_area_sqm, flat_model)
--        Resolves flat_id using the 6-field unique identity.
--
--     Rows that fail either join (unmatched block or unmatched
--     flat) are silently excluded. sp04 must be called first to
--     ensure all valid flats exist in public.hdb_flat.
--
-- Prerequisites:
--     - sp01_SchemaCreation.sql (public.resale_transaction exists)
--     - sp03_load_hdb_block.sql (public.hdb_block is populated)
--     - sp04_load_hdb_flat.sql  (public.hdb_flat is populated
--       with current batch — MUST be called before this procedure)
--     - staging.resale is loaded with current batch
--
-- Usage:
--     CALL public.sp_load_resale_transaction();
-- ============================================================

CREATE OR REPLACE PROCEDURE public.sp_load_resale_transaction()
LANGUAGE plpgsql
AS $$
DECLARE
    v_inserted INT;
BEGIN
    -- Insert transactions, resolving flat_id via double JOIN
    INSERT INTO public.resale_transaction
        (flat_id, transaction_year, transaction_month,
         remaining_lease_years, resale_price)
    SELECT f.flat_id,
           CAST(sr.transaction_year AS INT),
           CAST(sr.transaction_month AS INT),
           CAST(sr.remaining_lease_years AS INT),
           CAST(sr.resale_price AS INT)
    FROM staging.resale sr
    JOIN public.hdb_block hb
        ON sr.blk_no = hb.block_number
       AND sr.street = hb.street
    JOIN public.hdb_flat f
        ON hb.block_id       = f.block_id
       AND sr.flat_type      = f.flat_type
       AND CAST(sr.storey_min AS INT)               = f.storey_min
       AND CAST(sr.storey_max AS INT)               = f.storey_max
       AND CAST(sr.floor_area_sqm AS NUMERIC(6,1))  = f.floor_area_sqm
       AND sr.flat_model     = f.flat_model;

    GET DIAGNOSTICS v_inserted = ROW_COUNT;
    RAISE NOTICE 'Committed. Transactions inserted: %', v_inserted;

EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Rolled back: %', SQLERRM;
    RAISE;
END;
$$;
