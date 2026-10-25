-- ============================================================
-- sp08_load_proximity.sql
-- Stored Procedure: public.sp_load_proximity()
--
-- Purpose:
--     Load the block-to-hawker proximity relation from
--     staging.proximity into public.proximity. Both foreign keys are
--     resolved by natural key, exactly like sp04/sp05 resolve flat_id:
--       staging.proximity -> public.hdb_block   ON (blk_no, street)
--                         -> public.hawker_centre ON hawker_name
--     so the procedure does not care how the surrogate ids were
--     assigned upstream.
--
-- Source table:
--     staging.proximity (all TEXT columns, loaded by the loader)
--     Required columns: blk_no, street, hawker_name, distance_m, level_id
--
-- Target table:
--     public.proximity (block_id, hawker_id, level_id, distance_m)
--     PRIMARY KEY (block_id, hawker_id)
--
-- Unmatched rows:
--     The relation is computed over all 12,442 blocks in the HDB
--     property register, while hdb_block is loaded from the cleaned
--     register extract (10,194 blocks). Pairs on the remaining 2,248
--     blocks - commercial and other blocks that can never carry a
--     resale transaction - have no parent row and are skipped, with a
--     NOTICE reporting the count. Every block that IS in hdb_block has
--     at least one pair, so coverage of the analysable stock is total.
--
-- Level guard:
--     The schema's CHECK proximity_level_matches_distance binds
--     level_id to distance_m (1: <=500 m, 2: 500-1000 m, 3: >1000 m,
--     walking). A staged row that contradicts its band fails the load
--     instead of silently entering the database.
--
-- Deduplication:
--     NOT EXISTS on (block_id, hawker_id), so re-running is safe.
--
-- Prerequisites:
--     - sp01_SchemaCreation.sql (public.proximity table exists)
--     - sp03_load_hdb_block.sql   (public.hdb_block is populated)
--     - sp06_load_hawker_centre.sql (public.hawker_centre is populated)
--     - sp07_load_proximity_level.sql (public.proximity_level is populated)
--     - staging.proximity is loaded with the current batch
--
-- Usage:
--     CALL public.sp_load_proximity();
-- ============================================================

CREATE OR REPLACE PROCEDURE public.sp_load_proximity()
LANGUAGE plpgsql
AS $$
DECLARE
    v_inserted  INT;
    v_no_block  INT;
    v_no_hawker INT;
BEGIN
    -- Count staging rows that cannot resolve to a parent (for diagnostics)
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
