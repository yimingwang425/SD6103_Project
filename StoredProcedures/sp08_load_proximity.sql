-- ============================================================
-- sp08_load_proximity.sql
-- Stored Procedure: public.sp_load_proximity()
--
-- Load the block-to-hawker proximity relation from staging.proximity
-- into public.proximity. block_id and hawker_id are resolved by natural
-- key ((blk_no, street) and hawker_name), like sp04/sp05 resolve
-- flat_id. The relation covers all 12,442 registered blocks while
-- hdb_block holds 10,194: pairs on the other 2,248 blocks (no resale
-- transactions) have no parent row and are skipped, with a NOTICE.
-- The schema's CHECK binds level_id to distance_m, so a staged row
-- that contradicts its band fails the load instead of entering the
-- database. NOT EXISTS on (block_id, hawker_id) makes re-runs safe.
-- ============================================================

CREATE OR REPLACE PROCEDURE public.sp_load_proximity()
LANGUAGE plpgsql
AS $$
DECLARE
    v_inserted  INT;
    v_no_block  INT;
    v_no_hawker INT;
BEGIN
    -- count staging rows that cannot resolve to a parent (diagnostics)
    SELECT COUNT(*) INTO v_no_block
    FROM staging.proximity s
    LEFT JOIN public.hdb_block b
        ON s.blk_no = b.block_number
       AND s.street = b.street
    WHERE b.block_id IS NULL;

    SELECT COUNT(*) INTO v_no_hawker
    FROM staging.proximity s
    LEFT JOIN public.hawker_centre h
        ON s.hawker_name = h.hawker_name
    WHERE h.hawker_id IS NULL;

    IF v_no_block > 0 THEN
        RAISE NOTICE '% staging rows have no matching hdb_block (skipped)', v_no_block;
    END IF;
    IF v_no_hawker > 0 THEN
        RAISE NOTICE '% staging rows have no matching hawker_centre (skipped)', v_no_hawker;
    END IF;

    INSERT INTO public.proximity
        (block_id, hawker_id, level_id, distance_m)
    SELECT b.block_id,
           h.hawker_id,
           CAST(s.level_id AS INT),
           CAST(s.distance_m AS NUMERIC(8,2))
    FROM   staging.proximity s
    JOIN   public.hdb_block b
        ON s.blk_no = b.block_number
       AND s.street = b.street
    JOIN   public.hawker_centre h
        ON s.hawker_name = h.hawker_name
    WHERE  NOT EXISTS (
        SELECT 1
        FROM public.proximity p
        WHERE p.block_id = b.block_id
          AND p.hawker_id = h.hawker_id
    );

    GET DIAGNOSTICS v_inserted = ROW_COUNT;
    RAISE NOTICE 'Committed. Proximity rows inserted: %', v_inserted;

EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Rolled back: %', SQLERRM;
    RAISE;
END;
$$;
