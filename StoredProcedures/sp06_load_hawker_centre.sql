-- ============================================================
-- sp06_load_hawker_centre.sql
-- Stored Procedure: public.sp_load_hawker_centre()
--
-- Load hawker centre data from staging.hawker_centre into
-- public.hawker_centre. Empty strings become NULL: 17 centres have no
-- postal code and 23 have no stall counts (opened after the official
-- register's snapshot) - unknown is not "zero stalls", and Q5's
-- predicate excludes them. NOT EXISTS on hawker_name makes re-runs safe.
-- ============================================================

CREATE OR REPLACE PROCEDURE public.sp_load_hawker_centre()
LANGUAGE plpgsql
AS $$
DECLARE
    v_inserted INT;
BEGIN
    INSERT INTO public.hawker_centre
        (hawker_name, street, latitude, longitude, postal,
         no_of_mkt_produce_stalls, no_of_cooked_food_stalls)
    SELECT s.hawker_name,
           NULLIF(s.street, ''),
           CAST(s.latitude  AS NUMERIC(9,6)),
           CAST(s.longitude AS NUMERIC(9,6)),
           NULLIF(s.postal, ''),
           CAST(NULLIF(s.no_of_mkt_produce_stalls, '') AS INT),
           CAST(NULLIF(s.no_of_cooked_food_stalls, '') AS INT)
    FROM staging.hawker_centre s
    WHERE NOT EXISTS (
        SELECT 1
        FROM public.hawker_centre h
        WHERE h.hawker_name = s.hawker_name
    );

    GET DIAGNOSTICS v_inserted = ROW_COUNT;
    RAISE NOTICE 'Committed. Hawker centres inserted: %', v_inserted;

EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Rolled back: %', SQLERRM;
    RAISE;
END;
$$;
