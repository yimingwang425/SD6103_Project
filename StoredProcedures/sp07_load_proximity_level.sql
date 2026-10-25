-- ============================================================
-- sp07_load_proximity_level.sql
-- Stored Procedure: public.sp_load_proximity_level()
--
-- Purpose:
--     Load the three proximity class definitions from
--     staging.proximity_level into public.proximity_level. This is
--     static reference data, not a measured quantity: level_id is the
--     natural key (1/2/3) and travels with the row.
--
-- Source table:
--     staging.proximity_level (all TEXT columns, loaded by the loader)
--     Required columns: level_id, category, distance_min_m, distance_max_m
--
-- Target table:
--     public.proximity_level (level_id, category, distance_min_m,
--                             distance_max_m)
--
-- Bands are walking distances, (min, max]: a distance on a boundary
-- belongs to the lower level. level 3 has no upper bound, so its
-- distance_max_m is NULL (the CSV writes '' for it).
--
-- Deduplication:
--     NOT EXISTS on level_id, so re-running the procedure is safe.
--
-- Prerequisites:
--     - sp01_SchemaCreation.sql (public.proximity_level table exists)
--     - staging.proximity_level is loaded with the current batch
--
-- Usage:
--     CALL public.sp_load_proximity_level();
-- ============================================================

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
