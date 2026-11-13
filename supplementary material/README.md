# Supplementary material

One folder per task. The scripts run from inside these folders, but the
folder must stay inside the project repository: every script reads its input
from, and writes its CSV output to, the repository's `data/` directory two
levels up (`data/raw_data/`, `data/hawker_geo/`, `data/clean_data/`), so the
data is not duplicated here. Apart from that path, the files are identical
to the ones in the repository's `scripts/` and `StoredProcedures/`.

Requirements: PostgreSQL 16 or later (all measurements used 18.6) and Python 3
with pandas, numpy, psycopg (v3), psycopg2 and matplotlib.

Run the tasks in order: Task 1 and Task 2 build and load the database that
Task 3 and Task 4 query.

## Task1 – schema creation
- `create_database.sql` – creates the database `sd6103` and the seven tables:
  `psql -U postgres -f create_database.sql` (run from this folder).
- `sp01_SchemaCreation.sql` – the stored procedure `public.sp_build_schema()`
  with every `CREATE TABLE` statement, key and constraint.

## Task2 – code that generates the CSV files and loads them
Run each step from the folder that holds the script:
1. `scripts/hdb/hdb_clean.py` – HDB blocks and towns -> `hdb_block_clean.csv`, `hdb_town.csv`, `hdb_excluded.csv`
2. `scripts/resale/01_clean_resale.py`, then `02_prepare_staging.py` – the five resale files -> cleaned and staging CSVs
3. `scripts/hawker_geo/01_clean_hawker.py` – hawker centres -> `hawker_centre.csv`
4. `scripts/hawker_geo/02_walk_distances.py`, then
   `04_validate_distances.py --engine onemap --full --merge-only`, then
   `03_derive_proximity.py` – walking distances -> `proximity.csv`, `proximity_level.csv`.
   Both routing caches are in the repository, so this step needs no API
   token and makes no network calls.
5. `scripts/local/run_local.sh` – creates the database and runs
   `StoredProcedures/sp01`–`sp08` to load every CSV into its table (about 40 s).
   It drops and recreates the database `sd6103` on port 5544.
   `scripts/psql_load_notebook.ipynb` is the equivalent step-by-step loader.

## Task3 – queries and runtime measurements
- `task3_queries.sql` – Q1–Q6 in one file (Q1 has one statement per loading stage).
- `scripts/queries/Q1`–`Q6` – the same queries, one file each, as read by the benchmark.
- `scripts/queries/run_benchmark.py` – reloads the five resale files one by one
  and times every query 5 times per stage. Set `DB_CONFIG` to your connection first.
- `scripts/queries/plot_task3.py` – draws the runtime chart from the summary.
- `results/benchmark_results.csv` – raw measurements (150 rows).
- `results/benchmark_summary.csv`, `results/task3_benchmark.png` – averages and chart.

## Task4 – indexes and their effect
- `scripts/queries/create_indexes.sql` – the `CREATE INDEX` statements.
- `scripts/queries/run_index_benchmark.py` – times Q1–Q6 30 times with and
  without the indexes (connection via `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`).
- `scripts/queries/Q1`–`Q6` – the queries it times.
- `results/index_benchmark_raw.csv` – raw measurements (360 rows).
- `results/index_benchmark_summary.csv`, `results/index_benchmark.png` – averages and chart.
