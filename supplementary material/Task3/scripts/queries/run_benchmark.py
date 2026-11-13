#!/usr/bin/env python3
"""
SD6103 Project - Task 3 Performance Benchmark
==============================================
Automates incremental data loading and query timing.

Steps:
  1. Truncate hdb_flat & resale_transaction
  2. For each of the 5 resale files (chronological):
     a. Load staging CSV via COPY into temp table
     b. INSERT into hdb_flat and resale_transaction via JOIN
     c. Run Q1-Q6 each 5 times, record execution time
  3. Save all 150 measurements to CSV

Usage:
  cd <project_root>
  python3 scripts/queries/run_benchmark.py

Output:
  results/benchmark_results.csv   (150 rows of raw timing data)
  results/benchmark_summary.csv   (30 rows of averaged timing)
"""

import time, csv
from pathlib import Path
import psycopg2

# ── Config ──────────────────────────────────────────────────────
DB_CONFIG = dict(
    dbname="sd6103_project",
    user="your_user_name",
    host="localhost",
    port=5432,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# the staging CSVs stay in the repository's data/, above supplementary material/
REPO_ROOT = Path(__file__).resolve().parents[4]
DATA_DIR = REPO_ROOT / "data" / "clean_data" / "resale" / "incremental"
QUERY_DIR = PROJECT_ROOT / "scripts" / "queries"
RESULT_DIR = PROJECT_ROOT / "results"

RUNS_PER_QUERY = 5

# Chronological order: file1=1990-1999, ..., file5=2017-now
FILES = [
    {"label": "File1_1990-1999", "staging": "file1_staging.csv"},
    {"label": "File2_2000-2012", "staging": "file2_staging.csv"},
    {"label": "File3_2012-2014", "staging": "file3_staging.csv"},
    {"label": "File4_2015-2016", "staging": "file4_staging.csv"},
    {"label": "File5_2017-now",  "staging": "file5_staging.csv"},
]

QUERIES = {}
for i in range(1, 7):
    sql_files = list(QUERY_DIR.glob(f"Q{i}_*.sql"))
    if sql_files:
        raw = sql_files[0].read_text(encoding="utf-8")
        QUERIES[f"Q{i}"] = raw

# Q1 has 5 stage-specific queries (different 3-year ranges).
# Split them so each stage runs only the matching Q1.
Q1_STAGES = []
q1_raw = QUERIES["Q1"]
# Split on "-- ====== Stage" markers
import re
q1_blocks = re.split(r'-- ====== Stage \d', q1_raw)
for block in q1_blocks:
    # Extract only the SELECT statement
    select_match = re.search(r'(SELECT[\s\S]+?;)', block)
    if select_match:
        Q1_STAGES.append(select_match.group(1))


# ── Helpers ─────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def load_staging(conn, csv_path):
    """Load a staging CSV into hdb_flat and resale_transaction via JOIN."""
    cur = conn.cursor()

    # Create temp staging table
    cur.execute("""
        CREATE TEMP TABLE staging (
            blk_no VARCHAR(10), street VARCHAR(200), flat_type VARCHAR(20),
            storey_min INTEGER, storey_max INTEGER,
            floor_area_sqm NUMERIC(6,1), flat_model VARCHAR(40),
            transaction_year INTEGER, transaction_month INTEGER,
            remaining_lease_years INTEGER, resale_price INTEGER
        )
    """)

    # COPY CSV into staging
    with open(csv_path, "r", encoding="utf-8") as f:
        f.readline()  # skip header
        cur.copy_expert("COPY staging FROM STDIN WITH (FORMAT csv)", f)

    # staging → hdb_flat (deduplicated, matched by text)
    cur.execute("""
        INSERT INTO public.hdb_flat (block_id, flat_type, storey_min, storey_max,
                              floor_area_sqm, flat_model)
        SELECT DISTINCT b.block_id, s.flat_type, s.storey_min, s.storey_max,
               s.floor_area_sqm, s.flat_model
        FROM   staging s
        JOIN   public.hdb_block b ON s.blk_no = b.block_number
                                 AND s.street = b.street
        ON CONFLICT DO NOTHING
    """)

    # staging → resale_transaction (matched by text → block_id → flat_id)
    cur.execute("""
        INSERT INTO public.resale_transaction (flat_id, transaction_year, transaction_month,
                                        remaining_lease_years, resale_price)
        SELECT f.flat_id, s.transaction_year, s.transaction_month,
               s.remaining_lease_years, s.resale_price
        FROM   staging s
        JOIN   public.hdb_block b ON s.blk_no = b.block_number
                                 AND s.street = b.street
        JOIN   public.hdb_flat f  ON b.block_id = f.block_id
                                 AND s.flat_type = f.flat_type
                                 AND s.storey_min = f.storey_min
                                 AND s.storey_max = f.storey_max
                                 AND s.floor_area_sqm = f.floor_area_sqm
                                 AND s.flat_model = f.flat_model
    """)

    cur.execute("DROP TABLE staging")
    conn.commit()
    cur.close()


def get_table_counts(conn):
    """Return (flat_count, txn_count)."""
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM public.hdb_flat")
    flats = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM public.resale_transaction")
    txns = cur.fetchone()[0]
    cur.close()
    return flats, txns


def run_query_timed(conn, sql):
    """Execute a query and return wall-clock time in milliseconds."""
    cur = conn.cursor()
    t0 = time.perf_counter()
    cur.execute(sql)
    cur.fetchall()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    cur.close()
    return round(elapsed_ms, 3)


# ── Main ────────────────────────────────────────────────────────
def main():
    RESULT_DIR.mkdir(exist_ok=True)

    raw_results = []
    summary = []

    conn = get_conn()

    # Step 1: Truncate resale tables (keep town, hdb_block, hawker, proximity)
    print("Truncating hdb_flat and resale_transaction ...")
    cur = conn.cursor()
    cur.execute("TRUNCATE public.resale_transaction, public.hdb_flat CASCADE")
    conn.commit()
    # Verify tables are empty
    cur.execute("SELECT COUNT(*) FROM public.resale_transaction")
    assert cur.fetchone()[0] == 0, "resale_transaction not empty after TRUNCATE"
    cur.execute("SELECT COUNT(*) FROM public.hdb_flat")
    assert cur.fetchone()[0] == 0, "hdb_flat not empty after TRUNCATE"
    cur.close()
    print("Done.\n")

    for fi, finfo in enumerate(FILES, 1):
        label = finfo["label"]
        cumulative_label = f"After File {fi}"

        staging_path = DATA_DIR / finfo["staging"]

        print(f"{'='*50}")
        print(f"Loading {label} ...")

        load_staging(conn, staging_path)

        flats, txns = get_table_counts(conn)
        print(f"  hdb_flat: {flats:,}   resale_transaction: {txns:,}")

        # Run Q1-Q6, each 5 times
        for qname, sql in QUERIES.items():
            # Q1: use stage-specific query (different 3-year range per stage)
            if qname == "Q1":
                sql = Q1_STAGES[fi - 1]

            times = []
            for run in range(1, RUNS_PER_QUERY + 1):
                ms = run_query_timed(conn, sql)
                times.append(ms)
                raw_results.append([cumulative_label, flats, txns, qname, run, ms])

            avg_ms = round(sum(times) / len(times), 3)
            summary.append([cumulative_label, flats, txns, qname, avg_ms])
            print(f"  {qname}: {times}  avg={avg_ms} ms")

        print()

    conn.close()

    # Write CSVs
    raw_path = RESULT_DIR / "benchmark_results.csv"
    with open(raw_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stage", "total_flats", "total_transactions", "query", "run", "time_ms"])
        w.writerows(raw_results)
    print(f"Raw results saved to: {raw_path}")

    sum_path = RESULT_DIR / "benchmark_summary.csv"
    with open(sum_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stage", "total_flats", "total_transactions", "query", "avg_time_ms"])
        w.writerows(summary)
    print(f"Summary saved to: {sum_path}")

    print(f"\nTotal measurements: {len(raw_results)}")
    print("Done!")


if __name__ == "__main__":
    main()
