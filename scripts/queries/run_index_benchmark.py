#!/usr/bin/env python3
"""
SD6103 Project - Task 4 Index Performance Benchmark
====================================================
Compares Q1-Q6 runtime WITH vs WITHOUT indexes on the
largest database (all 5 files loaded).

Prerequisites:
  - All 5 resale files already loaded (run run_benchmark.py first)
  - benchmark_results.csv exists (Task 3 results)

Steps:
  1. Drop custom indexes (ensure clean state)
  2. Run Q1-Q6 x5 WITHOUT indexes → record timing
  3. Create indexes
  4. Run Q1-Q6 x5 WITH indexes → record timing
  5. Save comparison CSV

Usage:
  cd <project_root>
  python3 scripts/queries/run_index_benchmark.py

Output:
  results/index_benchmark_raw.csv      (60 rows: 6 queries x 5 runs x 2 conditions)
  results/index_comparison.csv         (6 rows: avg time with/without per query)
"""

import time, csv
from pathlib import Path
import psycopg2

# ── Config ──────────────────────────────────────────────────────
DB_CONFIG = dict(
    dbname="sd6103_project",
    user="postgres",
    host="localhost",
    port=5432,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
QUERY_DIR = PROJECT_ROOT / "scripts" / "queries"
RESULT_DIR = PROJECT_ROOT / "results"

RUNS_PER_QUERY = 5

# Load queries
QUERIES = {}
for i in range(1, 7):
    sql_files = list(QUERY_DIR.glob(f"Q{i}_*.sql"))
    if sql_files:
        QUERIES[f"Q{i}"] = sql_files[0].read_text(encoding="utf-8")

# Load index SQL
DROP_SQL = (QUERY_DIR / "drop_indexes.sql").read_text(encoding="utf-8")
CREATE_SQL = (QUERY_DIR / "create_indexes.sql").read_text(encoding="utf-8")


# ── Helpers ─────────────────────────────────────────────────────
def run_query_timed(conn, sql):
    cur = conn.cursor()
    t0 = time.perf_counter()
    cur.execute(sql)
    cur.fetchall()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    cur.close()
    return round(elapsed_ms, 3)


def run_all_queries(conn, condition_label):
    """Run Q1-Q6 x5 and return list of [condition, query, run, time_ms]."""
    results = []
    for qname, sql in QUERIES.items():
        times = []
        for run in range(1, RUNS_PER_QUERY + 1):
            ms = run_query_timed(conn, sql)
            times.append(ms)
            results.append([condition_label, qname, run, ms])
        avg_ms = round(sum(times) / len(times), 3)
        print(f"  {qname}: {times}  avg={avg_ms} ms")
    return results


# ── Main ────────────────────────────────────────────────────────
def main():
    RESULT_DIR.mkdir(exist_ok=True)
    conn = psycopg2.connect(**DB_CONFIG)

    # Verify data is loaded
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM hdb_flat")
    flats = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM resale_transaction")
    txns = cur.fetchone()[0]
    cur.close()
    print(f"Database: {flats:,} flats, {txns:,} transactions\n")

    if txns < 100000:
        print("WARNING: Database looks small. Make sure all 5 files are loaded.")
        print("Run 'python3 scripts/queries/run_benchmark.py' first.\n")

    all_raw = []

    # Phase 1: WITHOUT indexes
    print("=" * 50)
    print("Phase 1: Dropping indexes (no-index baseline)")
    print("=" * 50)
    cur = conn.cursor()
    for stmt in DROP_SQL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            cur.execute(stmt)
    conn.commit()
    cur.close()
    print("Indexes dropped. Running queries ...\n")

    results_no_idx = run_all_queries(conn, "without_index")
    all_raw.extend(results_no_idx)

    # Phase 2: WITH indexes
    print()
    print("=" * 50)
    print("Phase 2: Creating indexes")
    print("=" * 50)
    cur = conn.cursor()
    # Execute create_indexes.sql (skip lines starting with SELECT and --)
    for stmt in CREATE_SQL.split(";"):
        stmt = stmt.strip()
        if stmt and stmt.upper().startswith("CREATE INDEX"):
            cur.execute(stmt)
            idx_name = stmt.split("INDEX")[-1].split("ON")[0].strip()
            print(f"  Created: {idx_name}")
    conn.commit()
    cur.close()
    print("Indexes created. Running queries ...\n")

    results_with_idx = run_all_queries(conn, "with_index")
    all_raw.extend(results_with_idx)

    conn.close()

    # Save raw results
    raw_path = RESULT_DIR / "index_benchmark_raw.csv"
    with open(raw_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["condition", "query", "run", "time_ms"])
        w.writerows(all_raw)
    print(f"\nRaw results saved to: {raw_path}")

    # Compute summary comparison
    from collections import defaultdict
    agg = defaultdict(list)
    for row in all_raw:
        key = (row[0], row[1])  # (condition, query)
        agg[key].append(row[3])

    comp_path = RESULT_DIR / "index_comparison.csv"
    with open(comp_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["query", "avg_without_index_ms", "avg_with_index_ms", "speedup"])
        for qi in range(1, 7):
            qname = f"Q{qi}"
            no_idx = agg.get(("without_index", qname), [])
            wi_idx = agg.get(("with_index", qname), [])
            avg_no = round(sum(no_idx) / len(no_idx), 3) if no_idx else 0
            avg_wi = round(sum(wi_idx) / len(wi_idx), 3) if wi_idx else 0
            speedup = round(avg_no / avg_wi, 2) if avg_wi > 0 else 0
            w.writerow([qname, avg_no, avg_wi, speedup])
            print(f"  {qname}: no_idx={avg_no}ms  with_idx={avg_wi}ms  speedup={speedup}x")

    print(f"Comparison saved to: {comp_path}")
    print("\nDone!")


if __name__ == "__main__":
    main()
