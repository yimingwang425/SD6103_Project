-- Task 1: create the database and the seven tables.
-- Run with psql from this folder:
--     psql -U postgres -f create_database.sql
-- sp01_SchemaCreation.sql defines public.sp_build_schema(); the CALL
-- creates town, hdb_block, hdb_flat, resale_transaction, hawker_centre,
-- proximity_level and proximity with their keys and constraints.

DROP DATABASE IF EXISTS sd6103;
CREATE DATABASE sd6103;
\c sd6103

\i sp01_SchemaCreation.sql
CALL public.sp_build_schema();
