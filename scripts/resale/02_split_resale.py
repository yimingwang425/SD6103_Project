"""
Step 2: Prepare Staging CSVs for Database Loading
==================================================

Purpose:
    Transform cleaned resale CSVs into staging CSVs ready for database
    loading. Each staging CSV is a flat denormalised table; the actual
    split into hdb_flat + resale_transaction happens inside PostgreSQL
    via 04_incremental_load.sql (staging → JOIN hdb_block → INSERT).

    This script does NOT match block_id or split into two tables.
    It only reshapes columns:
      - month "2015-01" → transaction_year + transaction_month
      - block → blk_no,  street_name → street
      - drops: town, lease_commence_date (not needed for staging)

Input:
    data/src/resale/cleaned/file{1..5}_cleaned.csv  (from 01_clean_resale.py)

Output:
    data/clean_data/resale/incremental/
      file1_staging.csv  (1990-1999)
      file2_staging.csv  (2000-2012)
      file3_staging.csv  (2012-2014)
      file4_staging.csv  (2015-2016)
      file5_staging.csv  (2017-onwards)

    Staging CSV columns (matches the TEMP TABLE in 04_incremental_load.sql):
      blk_no, street, flat_type, storey_min, storey_max,
      floor_area_sqm, flat_model, transaction_year, transaction_month,
      remaining_lease_years, resale_price

Run:
    cd <project_root>
    python scripts/resale/02_split_resale.py
"""

import csv
import os

# ── Paths ──
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLEANED_DIR = os.path.join(PROJECT_ROOT, "data", "clean_data", "resale")
OUT_DIR = os.path.join(PROJECT_ROOT, "data", "clean_data", "resale", "incremental")
os.makedirs(OUT_DIR, exist_ok=True)

NUM_FILES = 5

STAGING_COLUMNS = [
    "blk_no", "street", "flat_type",
    "storey_min", "storey_max", "floor_area_sqm", "flat_model",
    "transaction_year", "transaction_month",
    "remaining_lease_years", "resale_price",
]


# ── Main ──
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
                "remaining_lease_years":  int(row["remaining_lease_years"]),
                "resale_price":          int(row["resale_price"]),
            })

    out_path = os.path.join(OUT_DIR, f"file{i}_staging.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=STAGING_COLUMNS)
        w.writeheader()
        w.writerows(staging_rows)

    print(f"File {i}: {total} rows")

print(f"\nOutput directory: {OUT_DIR}")
print("Done.")
