CREATE OR REPLACE PROCEDURE public.sp_build_schema(p_drop_first boolean DEFAULT false)
LANGUAGE plpgsql
AS $proc$
DECLARE
    run_start timestamptz := clock_timestamp();
BEGIN
    -- checking if table exist
    IF p_drop_first THEN
        DROP TABLE IF EXISTS public.hdb_block CASCADE;
        DROP TABLE IF EXISTS public.town CASCADE;
        RAISE NOTICE 'dropped existing tables (children first)';
    END IF;

    -- creating town schema
    CREATE TABLE IF NOT EXISTS public.town (
        town_id INT GENERATED ALWAYS AS IDENTITY,
        town_name VARCHAR(50) NOT NULL,

        CONSTRAINT pk_town PRIMARY KEY (town_id),
        CONSTRAINT uq_town_name UNIQUE (town_name)
    );
    RAISE NOTICE 'town schema created';
	
	-- creating hdb_block schema
    CREATE TABLE IF NOT EXISTS public.hdb_block (
        block_id INT GENERATED ALWAYS AS IDENTITY,
        town_id INT NOT NULL,
        block_number VARCHAR(10)  NOT NULL,
        street VARCHAR(200) NOT NULL,
        lease_commence_year INT,
        latitude NUMERIC(9,6),
        longitude NUMERIC(9,6),
        postal VARCHAR(6),

        CONSTRAINT pk_hdb_block PRIMARY KEY (block_id),
        CONSTRAINT fk_block_town FOREIGN KEY (town_id) REFERENCES public.town (town_id),
        CONSTRAINT uq_block_address UNIQUE (block_number, street),
        CONSTRAINT ck_block_lat CHECK (latitude  BETWEEN 1.15  AND 1.48),
        CONSTRAINT ck_block_lon CHECK (longitude BETWEEN 103.6 AND 104.42),
        CONSTRAINT ck_block_lease CHECK (lease_commence_year BETWEEN 1900 AND 2100),
        CONSTRAINT ck_block_postal CHECK (postal ~ '^[0-9]{6}$')
    );
    RAISE NOTICE 'hdb_block schema created';


    -- creating required indexes
    CREATE INDEX IF NOT EXISTS idx_hdb_block_town ON public.hdb_block (town_id);
    RAISE NOTICE 'indexes created';
    RAISE NOTICE '--------------------------------------------';
    RAISE NOTICE 'schema built in %ms',
        round(extract(epoch from clock_timestamp() - run_start) * 1000);
END;
$proc$;