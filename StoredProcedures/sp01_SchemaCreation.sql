CREATE OR REPLACE PROCEDURE public.sp_build_schema(p_drop_first boolean DEFAULT false)
LANGUAGE plpgsql
AS $proc$
DECLARE
    run_start timestamptz := clock_timestamp();
BEGIN
    -- checking if table exist
    IF p_drop_first THEN
        DROP TABLE IF EXISTS public.proximity CASCADE;
        DROP TABLE IF EXISTS public.resale_transaction CASCADE;
        DROP TABLE IF EXISTS public.hdb_flat CASCADE;
        DROP TABLE IF EXISTS public.hawker_centre CASCADE;
        DROP TABLE IF EXISTS public.proximity_level CASCADE;
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
        year_completed INT,
        latitude NUMERIC(9,6),
        longitude NUMERIC(9,6),
        postal VARCHAR(6),

        CONSTRAINT pk_hdb_block PRIMARY KEY (block_id),
        CONSTRAINT fk_block_town FOREIGN KEY (town_id) REFERENCES public.town (town_id),
        CONSTRAINT uq_block_address UNIQUE (block_number, street),
        CONSTRAINT ck_block_lat CHECK (latitude  BETWEEN 1.15  AND 1.48),
        CONSTRAINT ck_block_lon CHECK (longitude BETWEEN 103.6 AND 104.42),
        CONSTRAINT ck_block_year CHECK (year_completed BETWEEN 1900 AND 2100),
        CONSTRAINT ck_block_postal CHECK (postal ~ '^[0-9]{6}$')
    );
    RAISE NOTICE 'hdb_block schema created';


    -- creating required indexes
    -- CREATE INDEX IF NOT EXISTS idx_hdb_block_town ON public.hdb_block (town_id);
    -- RAISE NOTICE 'indexes created';

    -- creating hdb_flat schema; resale rows have no unit number, so identical units collapse into one row
    CREATE TABLE IF NOT EXISTS public.hdb_flat (
        flat_id        INT          GENERATED ALWAYS AS IDENTITY,
        block_id       INT          NOT NULL,
        flat_type      VARCHAR(20)  NOT NULL,
        storey_min     INT          NOT NULL,
        storey_max     INT          NOT NULL,
        floor_area_sqm NUMERIC(9,2) NOT NULL,
        flat_model     VARCHAR(40)  NOT NULL,

        CONSTRAINT pk_hdb_flat PRIMARY KEY (flat_id),
        CONSTRAINT fk_flat_block FOREIGN KEY (block_id) REFERENCES public.hdb_block (block_id),
        CONSTRAINT uq_flat_identity UNIQUE (block_id, flat_type, storey_min,
                                            storey_max, floor_area_sqm, flat_model)
    );
    RAISE NOTICE 'hdb_flat schema created';

    -- creating resale_transaction schema; one row per transaction
    CREATE TABLE IF NOT EXISTS public.resale_transaction (
        transaction_id        INT           GENERATED ALWAYS AS IDENTITY,
        flat_id               INT           NOT NULL,
        transaction_year      INT           NOT NULL,
        transaction_month     INT           NOT NULL,
        remaining_lease_years INT           NOT NULL,
        resale_price          NUMERIC(12,2) NOT NULL,

        CONSTRAINT pk_resale_transaction PRIMARY KEY (transaction_id),
        CONSTRAINT fk_txn_flat FOREIGN KEY (flat_id) REFERENCES public.hdb_flat (flat_id),
        CONSTRAINT ck_txn_month CHECK (transaction_month BETWEEN 1 AND 12),
        CONSTRAINT ck_txn_lease CHECK (remaining_lease_years BETWEEN 0 AND 99),
        CONSTRAINT ck_txn_price CHECK (resale_price > 0)
    );
    -- CREATE INDEX IF NOT EXISTS resale_transaction_flat_idx
    --     ON public.resale_transaction (flat_id);
    -- CREATE INDEX IF NOT EXISTS resale_transaction_month_idx
    --     ON public.resale_transaction (transaction_year, transaction_month);
    RAISE NOTICE 'resale_transaction schema created';

    -- creating hawker_centre schema; stall counts NULL for the 23 centres opened after the register snapshot
    CREATE TABLE IF NOT EXISTS public.hawker_centre (
        hawker_id                INT          GENERATED ALWAYS AS IDENTITY,
        hawker_name              VARCHAR(100) NOT NULL,
        street                   VARCHAR(200),
        latitude                 NUMERIC(9,6) NOT NULL,
        longitude                NUMERIC(9,6) NOT NULL,
        postal                   VARCHAR(6),
        no_of_mkt_produce_stalls INT,
        no_of_cooked_food_stalls INT,

        CONSTRAINT pk_hawker_centre PRIMARY KEY (hawker_id),
        CONSTRAINT uq_hawker_name UNIQUE (hawker_name),
        CONSTRAINT ck_hawker_postal CHECK (postal ~ '^[0-9]{6}$')
    );
    RAISE NOTICE 'hawker_centre schema created';

    -- creating proximity_level schema; walking bands (min, max], top level open-ended
    CREATE TABLE IF NOT EXISTS public.proximity_level (
        level_id       INT          PRIMARY KEY,
        category       VARCHAR(50)  NOT NULL UNIQUE,
        distance_min_m NUMERIC(8,2),
        distance_max_m NUMERIC(8,2)
    );
    RAISE NOTICE 'proximity_level schema created';

    -- creating proximity schema; each pair stores the walking band it falls in
    CREATE TABLE IF NOT EXISTS public.proximity (
        block_id   INT NOT NULL,
        hawker_id  INT NOT NULL,
        level_id   INT NOT NULL,

        CONSTRAINT pk_proximity PRIMARY KEY (block_id, hawker_id),
        CONSTRAINT fk_prox_block  FOREIGN KEY (block_id)  REFERENCES public.hdb_block (block_id),
        CONSTRAINT fk_prox_hawker FOREIGN KEY (hawker_id) REFERENCES public.hawker_centre (hawker_id),
        CONSTRAINT fk_prox_level  FOREIGN KEY (level_id)  REFERENCES public.proximity_level (level_id)
    );
    RAISE NOTICE 'proximity schema created';

    RAISE NOTICE '--------------------------------------------';
    RAISE NOTICE 'schema built in %ms',
        round(extract(epoch from clock_timestamp() - run_start) * 1000);
END;
$proc$;
