"""
02_prepare_staging.py — Prepare Staging CSVs for Database Loading
=================================================================

Overview:
    Transforms cleaned resale CSVs (from 01_clean_resale.py) into staging
    CSVs that can be directly loaded into a PostgreSQL temporary table
    via COPY. The staging format is a flat denormalised table; the actual
    normalisation into hdb_flat + resale_transaction happens inside
    PostgreSQL via 04_incremental_load.sql using staging + JOIN.

    This script does NOT:
      - Match or resolve block_id (done in SQL via JOIN to hdb_block)
      - Split data into separate table CSVs (done in SQL via INSERT...SELECT)
      - Perform deduplication (done in SQL via ON CONFLICT DO NOTHING)

Column mapping from cleaned -> staging:
    month ("2015-01")    -> transaction_year (2015) + transaction_month (1)
    block                -> blk_no
    street_name          -> street
    town                 -> (dropped, not needed for staging)
    lease_commence_date  -> (dropped, not needed for staging)
    All other columns    -> carried over unchanged

Input:
    data/clean_data/resale/file{1..5}_cleaned.csv
    (produced by 01_clean_resale.py, in chronological order)

Output:
    data/clean_data/resale/incremental/
      file1_staging.csv  (1990-1999)
      file2_staging.csv  (2000-2012)
      file3_staging.csv  (2012-2014)
      file4_staging.csv  (2015-2016)
      file5_staging.csv  (2017-onwards)

    Staging CSV columns (must match the TEMP TABLE definition in
    04_incremental_load.sql and run_benchmark.py):
      blk_no, street, flat_type, storey_min, storey_max,
      floor_area_sqm, flat_model, transaction_year, transaction_month,
      remaining_lease_years, resale_price

Dependencies:
    - 01_clean_resale.py must be run first

Usage:
    cd <project_root>
    python scripts/resale/02_prepare_staging.py
"""

import csv
import os

# ── Configuration ────────────────────────────────────────────────
# supplementary material/Task2/scripts/resale/ -> repo root, where data/ lives
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
CLEANED_DIR = os.path.join(PROJECT_ROOT, "data", "clean_data", "resale")
OUT_DIR = os.path.join(PROJECT_ROOT, "data", "clean_data", "resale", "incremental")
os.makedirs(OUT_DIR, exist_ok=True)

NUM_FILES = 5

# Column order for staging output CSVs — must match the TEMP TABLE
# definition in 04_incremental_load.sql
STAGING_COLUMNS = [
    "blk_no", "street", "flat_type",
    "storey_min", "storey_max", "floor_area_sqm", "flat_model",
    "transaction_year", "transaction_month",
    "remaining_lease_years", "resale_price",
]


# ── Main processing loop ────────────────────────────────────────
for i in range(1, NUM_FILES + 1):
    cleaned_path = os.path.join(CLEANED_DIR, f"file{i}_cleaned.csv")
    staging_rows = []
    total = 0

    with open(cleaned_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            total += 1
            year, month = row["month"].split("-")

            staging_rows.append({
                "blk_no":                row["block"],
                "street":                row["street_name"],
                "flat_type":             row["flat_type"],
                "storey_min":            int(row["storey_min"]),
                "storey_max":            int(row["storey_max"]),
                "floor_area_sqm":        row["floor_area_sqm"],
                "flat_model":            row["flat_model"],
                "transaction_year":      int(year),
                "transaction_month":     int(month),
                "remaining_lease_years": int(row["remaining_lease_years"]),
                "resale_price":          int(row["resale_price"]),
            })

    out_path = os.path.join(OUT_DIR, f"file{i}_staging.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=STAGING_COLUMNS)
        w.writeheader()
        w.writerows(staging_rows)

    print(f"File {i}: {total:,} rows")

print(f"\nOutput directory: {OUT_DIR}")
print("Done.")
