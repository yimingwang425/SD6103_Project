CREATE OR REPLACE PROCEDURE public.sp_load_proximity()
LANGUAGE plpgsql
AS $$
DECLARE
    v_inserted  INT;
    v_no_block  INT;
    v_no_hawker INT;
BEGIN
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
