CREATE OR REPLACE PROCEDURE public.sp_load_town()
LANGUAGE plpgsql
AS $$
DECLARE
    v_inserted INT;
BEGIN
    INSERT INTO public.town (town_name)
    SELECT DISTINCT UPPER(TRIM(s.town_name))
    FROM staging.town s
    WHERE NULLIF(TRIM(s.town_name), '') IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
          FROM public.town t
          WHERE t.town_name = UPPER(TRIM(s.town_name))
      );

    GET DIAGNOSTICS v_inserted = ROW_COUNT;
    RAISE NOTICE 'Committed. Towns inserted: %', v_inserted;

EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Rolled back: %', SQLERRM;
    RAISE;
END;
$$;