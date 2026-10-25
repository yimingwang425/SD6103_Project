"""
01_clean_resale.py — Data Cleaning for HDB Resale Transactions
===============================================================

Overview:
    Cleans 5 raw HDB resale CSV files from data.gov.sg, resolving format
    inconsistencies across files collected over different time periods.
    Produces one standardised cleaned CSV per file in chronological order.

Input:
    5 raw CSV files from data/raw_data/ResaleFlatPrices/:
      File 1: "Resale Flat Prices (Based on Approval Date), 1990 - 1999.csv"
      File 2: "Resale Flat Prices (Based on Approval Date), 2000 - Feb 2012.csv"
      File 3: "Resale Flat Prices (Based on Registration Date), From Mar 2012 to Dec 2014.csv"
      File 4: "Resale Flat Prices (Based on Registration Date), From Jan 2015 to Dec 2016.csv"
      File 5: "Resale flat prices based on registration date from Jan-2017 onwards.csv"

    Schema differences across files:
      - Files 1-3 (1990-2014): no remaining_lease column
      - File 4 (2015-2016):    remaining_lease as integer (e.g. 70)
      - File 5 (2017-onwards): remaining_lease as string  (e.g. "61 years 04 months")

Output:
    data/clean_data/resale/
      file1_cleaned.csv  (1990-1999)     <- chronological order
      file2_cleaned.csv  (2000-2012)
      file3_cleaned.csv  (2012-2014)
      file4_cleaned.csv  (2015-2016)
      file5_cleaned.csv  (2017-onwards)
      removed.csv        (rows removed during cleaning, with reasons)

    Cleaned CSV columns:
      month, town, flat_type, block, street_name, storey_min, storey_max,
      floor_area_sqm, flat_model, lease_commence_date, remaining_lease_years,
      resale_price

Transformations applied:
    1. String standardisation: all string columns are stripped of whitespace
       and converted to UPPERCASE, ensuring consistent matching with the
       hdb_block table (whose block_number and street are uppercase).
    2. flat_type normalisation: "MULTI GENERATION" -> "MULTI-GENERATION"
       (hyphen added to match the canonical HDB naming convention).
    3. storey_range parsing: "10 TO 12" -> storey_min=10, storey_max=12.
    4. remaining_lease unification: converted to integer years.
       - Missing (Files 1-3): computed as 99 - transaction_year + lease_commence_date.
       - Integer (File 4): used directly.
       - String (File 5): "61 years 04 months" -> 61 + round(4/12) = 61.
         Months >= 6 are rounded up (e.g. "61 years 07 months" -> 62).
    5. resale_price: cast to integer (some source files store as float).

Rows removed (logged to removed.csv with reason):
    - remaining_lease unparseable       -> "remaining_lease parse failed"
    - remaining_lease_years > 99        -> "remaining_lease > 99"
    - storey_range format unrecognised  -> "storey_range parse failed"

Dependencies:
    - Raw CSV files must exist in data/raw_data/ResaleFlatPrices/

Usage:
    cd <project_root>
    python scripts/resale/01_clean_resale.py
"""

import csv
import os
import re

# ── Configuration ────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW_DIR = os.path.join(PROJECT_ROOT, "data", "raw_data", "ResaleFlatPrices")
OUT_DIR = os.path.join(PROJECT_ROOT, "data", "clean_data", "resale")
os.makedirs(OUT_DIR, exist_ok=True)

# Raw files listed in CHRONOLOGICAL order (File 1 = oldest)
RAW_FILES = [
    "Resale Flat Prices (Based on Approval Date), 1990 - 1999.csv",
    "Resale Flat Prices (Based on Approval Date), 2000 - Feb 2012.csv",
    "Resale Flat Prices (Based on Registration Date), From Mar 2012 to Dec 2014.csv",
    "Resale Flat Prices (Based on Registration Date), From Jan 2015 to Dec 2016.csv",
    "Resale flat prices based on registration date from Jan-2017 onwards.csv",
]

# Column order for cleaned output CSVs
OUT_COLUMNS = [
    "month", "town", "flat_type", "block", "street_name",
    "storey_min", "storey_max", "floor_area_sqm", "flat_model",
    "lease_commence_date", "remaining_lease_years", "resale_price",
]


# ── Helper functions ─────────────────────────────────────────────
def parse_remaining_lease(val, month_str, lease_commence):
    """Convert remaining_lease to integer years.

    Three cases depending on source file:
      1. Missing/empty (Files 1-3): compute as 99 - txn_year + lease_commence_date
      2. Integer string (File 4):   return as-is
      3. "Y years M months" (File 5): return Y + round(M / 12)
         e.g. "61 years 04 months" -> 61,  "61 years 07 months" -> 62

    Returns None if the value cannot be parsed (row will be removed).
    """
    if val is None or str(val).strip() == "":
        txn_year = int(month_str[:4])
        return 99 - txn_year + int(lease_commence)

    val_str = str(val).strip()

    if val_str.isdigit():
        return int(val_str)

    match = re.match(r"(\d+)\s+years?(?:\s+(\d+)\s+months?)?", val_str)
    if match:
        years = int(match.group(1))
        months = int(match.group(2)) if match.group(2) else 0
        return years + (1 if months >= 6 else 0)

    return None


def clean_row(row):
    """Clean and standardise a single raw row.

    Returns:
        (cleaned_dict, None) on success
        (None, reason_string) on failure — row will be logged and removed
    """
    # flat_type: uppercase, fix missing hyphen
    flat_type = row["flat_type"].strip().upper()
    if flat_type == "MULTI GENERATION":
        flat_type = "MULTI-GENERATION"

    # flat_model: uppercase for consistent deduplication
    flat_model = row["flat_model"].strip().upper()

    # storey_range: "10 TO 12" -> (10, 12)
    parts = row["storey_range"].strip().split(" TO ")
    if len(parts) != 2:
        return None, "storey_range parse failed"
    storey_min, storey_max = int(parts[0]), int(parts[1])

    # remaining_lease: unify to integer years
    raw_lease = row.get("remaining_lease")
    remaining = parse_remaining_lease(raw_lease, row["month"], row["lease_commence_date"])
    if remaining is None:
        return None, "remaining_lease parse failed"
    if remaining > 99:
        return None, "remaining_lease > 99"

    cleaned = {
        "month":                  row["month"].strip(),
        "town":                   row["town"].strip().upper(),
        "flat_type":              flat_type,
        "block":                  str(row["block"]).strip().upper(),
        "street_name":            row["street_name"].strip().upper(),
        "storey_min":             storey_min,
        "storey_max":             storey_max,
        "floor_area_sqm":         row["floor_area_sqm"].strip(),
        "flat_model":             flat_model,
        "lease_commence_date":    int(row["lease_commence_date"]),
        "remaining_lease_years":  remaining,
        "resale_price":           int(float(row["resale_price"])),
    }
    return cleaned, None


# ── Main processing loop ────────────────────────────────────────
all_removed = []

for i, filename in enumerate(RAW_FILES, start=1):
    filepath = os.path.join(RAW_DIR, filename)
    cleaned_rows = []
    total = 0
    bad = 0

    with open(filepath, newline="", encoding="utf-8") as f:
        for raw_row in csv.DictReader(f):
            total += 1
            result, reason = clean_row(raw_row)
            if result is None:
                raw_row["removal_reason"] = reason
                raw_row["source_file"] = filename
                all_removed.append(raw_row)
                bad += 1
            else:
                cleaned_rows.append(result)

    out_path = os.path.join(OUT_DIR, f"file{i}_cleaned.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUT_COLUMNS)
        w.writeheader()
        w.writerows(cleaned_rows)

    print(f"\nFile {i}: {filename}")
    print(f"  Input:    {total:,} rows")
    print(f"  Cleaned:  {len(cleaned_rows):,} rows")
    print(f"  Removed:  {bad} rows")

# Write all removed rows to a single file for inspection
removed_path = os.path.join(OUT_DIR, "removed.csv")
if all_removed:
    all_keys = list(all_removed[0].keys())
    with open(removed_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_removed)

print(f"\nAll removed rows: {len(all_removed)} -> {removed_path}")
print("Done.")
