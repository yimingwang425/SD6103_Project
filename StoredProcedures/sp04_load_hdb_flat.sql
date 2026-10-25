-- ============================================================
-- sp04_load_hdb_flat.sql
-- Stored Procedure: public.sp_load_hdb_flat()
--
-- Purpose:
--     Load HDB flat data from staging.resale into public.hdb_flat.
--     Resolves block_id by joining staging rows to public.hdb_block
--     on (blk_no = block_number, street = street). Inserts only
--     distinct flat combinations not already present in hdb_flat.
--
-- Source table:
--     staging.resale (all TEXT columns, loaded by run_load.py)
--     Required columns: blk_no, street, flat_type, storey_min,
--                       storey_max, floor_area_sqm, flat_model
--
-- Target table:
--     public.hdb_flat (block_id, flat_type, storey_min, storey_max,
--                      floor_area_sqm, flat_model)
--     UNIQUE constraint: uq_flat_identity
--         (block_id, flat_type, storey_min, storey_max,
--          floor_area_sqm, flat_model)
--
-- Deduplication:
--     Uses NOT EXISTS to skip flat combinations that already exist
--     in public.hdb_flat. Combined with SELECT DISTINCT to avoid
--     duplicates within the staging batch itself.
--
-- Unmatched rows:
--     Staging rows whose (blk_no, street) has no match in
--     public.hdb_block are silently skipped. A NOTICE reports the
--     count of unmatched rows for diagnostics. These typically
--     correspond to older or demolished blocks absent from the
--     HDB geolocation dataset.
--
-- Prerequisites:
--     - sp01_SchemaCreation.sql (public.hdb_flat table exists)
--     - sp03_load_hdb_block.sql (public.hdb_block is populated)
--     - staging.resale is loaded with current batch
--
-- Usage:
--     CALL public.sp_load_hdb_flat();
-- ============================================================

CREATE OR REPLACE PROCEDURE public.sp_load_hdb_flat()
LANGUAGE plpgsql
AS $$
DECLARE
    v_unmatched INT;
    v_inserted  INT;
BEGIN
    -- Count staging rows with no matching hdb_block (for diagnostics)
    SELECT COUNT(*) INTO v_unmatched
    FROM staging.resale sr
    LEFT JOIN public.hdb_block hb
        ON sr.blk_no = hb.block_number
       AND sr.street = hb.street
    WHERE hb.block_id IS NULL;

    IF v_unmatched > 0 THEN
        RAISE NOTICE '% staging rows have no matching hdb_block (skipped)', v_unmatched;
    END IF;

    -- Insert distinct flat combinations, skip existing ones
    INSERT INTO public.hdb_flat
        (block_id, flat_type, storey_min, storey_max, floor_area_sqm, flat_model)
    SELECT DISTINCT
           hb.block_id,
           sr.flat_type,
           CAST(sr.storey_min AS INT),
           CAST(sr.storey_max AS INT),
           CAST(sr.floor_area_sqm AS NUMERIC(6,1)),
           sr.flat_model
    FROM staging.resale sr
    JOIN public.hdb_block hb
        ON sr.blk_no = hb.block_number
       AND sr.street = hb.street
    WHERE NOT EXISTS (
        SELECT 1
        FROM public.hdb_flat f
        WHERE f.block_id       = hb.block_id
          AND f.flat_type      = sr.flat_type
          AND f.storey_min     = CAST(sr.storey_min AS INT)
          AND f.storey_max     = CAST(sr.storey_max AS INT)
          AND f.floor_area_sqm = CAST(sr.floor_area_sqm AS NUMERIC(6,1))
          AND f.flat_model     = sr.flat_model
    );

    GET DIAGNOSTICS v_inserted = ROW_COUNT;
    RAISE NOTICE 'Committed. Flats inserted: %', v_inserted;

EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Rolled back: %', SQLERRM;
    RAISE;
END;
$$;
