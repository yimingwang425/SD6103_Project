CREATE OR REPLACE PROCEDURE public.sp_load_hdb_block()
LANGUAGE plpgsql
AS $$
DECLARE
    v_unmatched INT;
    v_inserted  INT;
BEGIN
    -- Stop if any town names don't match
    SELECT COUNT(*) INTO v_unmatched
    FROM staging.hdb_block sh
    LEFT JOIN public.town pt
        ON sh.town_name = pt.town_name
    WHERE pt.town_id IS NULL;

    IF v_unmatched > 0 THEN
        RAISE EXCEPTION '% staging rows have no matching town', v_unmatched;
    END IF;

    INSERT INTO public.hdb_block
        (town_id, block_number, street, year_completed, latitude, longitude, postal)
    SELECT pt.town_id,
           sh.block_number,
           sh.street,
           CAST(sh.year_completed AS INT),
           CAST(sh.latitude AS NUMERIC(9,6)),
           CAST(sh.longitude AS NUMERIC(9,6)),
           sh.postal
    FROM staging.hdb_block sh
    JOIN public.town pt
        ON sh.town_name = pt.town_name
    WHERE NOT EXISTS (
        SELECT 1
        FROM public.hdb_block hb
        WHERE hb.block_number = sh.block_number
          AND hb.street       = sh.street
          AND hb.postal IS NOT DISTINCT FROM sh.postal
    );

    GET DIAGNOSTICS v_inserted = ROW_COUNT;
    RAISE NOTICE 'Committed. Rows inserted: %', v_inserted;

EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Rolled back: %', SQLERRM;
    RAISE;
END;
$$;