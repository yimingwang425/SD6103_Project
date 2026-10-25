CREATE OR REPLACE PROCEDURE public.sp_load_proximity_level()
LANGUAGE plpgsql
AS $$
DECLARE
    v_inserted INT;
BEGIN
    INSERT INTO public.proximity_level
        (level_id, category, distance_min_m, distance_max_m)
    SELECT CAST(s.level_id AS INT),
           s.category,
           CAST(s.distance_min_m AS NUMERIC(8,2)),
           CAST(NULLIF(s.distance_max_m, '') AS NUMERIC(8,2))
    FROM staging.proximity_level s
    WHERE NOT EXISTS (
        SELECT 1
        FROM public.proximity_level p
        WHERE p.level_id = CAST(s.level_id AS INT)
    );

    GET DIAGNOSTICS v_inserted = ROW_COUNT;
    RAISE NOTICE 'Committed. Proximity levels inserted: %', v_inserted;

EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Rolled back: %', SQLERRM;
    RAISE;
END;
$$;
