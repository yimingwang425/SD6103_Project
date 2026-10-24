"""
Step 1 of 2: Data Cleaning for HDB Resale Transaction Data
==========================================================

Purpose:
    Clean raw HDB resale CSV files from data.gov.sg. This script handles
    format inconsistencies across the 5 raw files and outputs one cleaned
    CSV per file, preserving all valid rows.

Input:
    5 raw CSV files under data/raw_data/ResaleFlatPrices/
    The files have different schemas:
      - Files 1,2,4 (1990-1999, 2000-2012, 2012-2014): no remaining_lease column
      - File 3 (2015-2016): remaining_lease as integer (e.g. 70)
      - File 5 (2017-onwards): remaining_lease as string (e.g. "61 years 04 months")

Output:
    data/src/resale/cleaned/
      file1_cleaned.csv  (2015-2016)
      file2_cleaned.csv  (2012-2014)
      file3_cleaned.csv  (1990-1999)
      file4_cleaned.csv  (2000-2012)
      file5_cleaned.csv  (2017-onwards)
      removed.csv        (all rows removed with reasons)

    Cleaned CSV columns:
      month, town, flat_type, block, street_name, storey_min, storey_max,
      floor_area_sqm, flat_model, lease_commence_date, remaining_lease_years,
      resale_price

Transformations applied:
    1. flat_type: "MULTI GENERATION" -> "MULTI-GENERATION"
    2. flat_model: title-cased, with special handling for "DBSS" and "3Gen"
    3. storey_range: split "10 TO 12" into storey_min=10, storey_max=12
    4. remaining_lease: unified to integer years
       - Missing: computed as 99 - transaction_year + lease_commence_date
       - Integer: used directly
       - String ("61 years 04 months"): parsed, months truncated
    5. resale_price: converted to integer (some files have float values)
    6. All string columns: stripped and uppercased

Rows removed (with reason logged):
    - remaining_lease fails to parse -> removal_reason = "remaining_lease parse failed"
    - remaining_lease_years > 99    -> removal_reason = "remaining_lease > 99"
    - storey_range fails to split   -> removal_reason = "storey_range parse failed"

Dependencies:
    None (only uses raw data files)

Run:
    python data/src/resale/clean.py
"""

import csv
import os
import re

# ── Paths ──
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW_DIR = os.path.join(BASE, "raw_data", "ResaleFlatPrices")
OUT_DIR = os.path.join(BASE, "src", "resale", "cleaned")
os.makedirs(OUT_DIR, exist_ok=True)

# 5 raw files in loading order (smallest first, as recommended by project guide)
RAW_FILES = [
    "Resale Flat Prices (Based on Registration Date), From Jan 2015 to Dec 2016.csv",
    "Resale Flat Prices (Based on Registration Date), From Mar 2012 to Dec 2014.csv",
    "Resale Flat Prices (Based on Approval Date), 1990 - 1999.csv",
    "Resale Flat Prices (Based on Approval Date), 2000 - Feb 2012.csv",
    "Resale flat prices based on registration date from Jan-2017 onwards.csv",
]

OUT_COLUMNS = [
    "month", "town", "flat_type", "block", "street_name",
    "storey_min", "storey_max", "floor_area_sqm", "flat_model",
    "lease_commence_date", "remaining_lease_years", "resale_price",
]


# ── Helpers ──
def parse_remaining_lease(val, month_str, lease_commence):
    """Convert remaining_lease to integer years.
    - Missing/empty: compute from 99 - txn_year + lease_commence_date
    - Integer string: return as int
    - "61 years 04 months": parse and return years portion
    - Unparseable: return None
    """
    if val is None or str(val).strip() == "":
        txn_year = int(month_str[:4])
        return 99 - txn_year + int(lease_commence)
    val_str = str(val).strip()
    if val_str.isdigit():
        return int(val_str)
    match = re.match(r"(\d+)\s+years?(?:\s+(\d+)\s+months?)?", val_str)
    if match:
        return int(match.group(1))
    return None


def clean_row(row):
    """Clean a single raw row.
    Returns (cleaned_dict, None) on success, or (None, reason) on failure.
    """
    # flat_type: standardise
    flat_type = row["flat_type"].strip().upper()
    if flat_type == "MULTI GENERATION":
        flat_type = "multi_generation"

    # flat_model: title case with special handling
    flat_model = row["flat_model"].strip().title()
    if flat_model == "Dbss":
        flat_model = "dbss"

    # storey_range: split into min/max
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
        "town":                   row["town"].strip().lower(),
        "flat_type":              flat_type,
        "block":                  str(row["block"]).strip().lower(),
        "street_name":            row["street_name"].strip().lower(),
        "storey_min":             storey_min,
        "storey_max":             storey_max,
        "floor_area_sqm":         row["floor_area_sqm"].strip(),
        "flat_model":             flat_model,
        "lease_commence_date":    int(row["lease_commence_date"]),
        "remaining_lease_years":  remaining,
        "resale_price":           int(float(row["resale_price"])),
    }
    return cleaned, None


# ── Main ──
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

    # Write cleaned CSV
    out_path = os.path.join(OUT_DIR, f"file{i}_cleaned.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUT_COLUMNS)
        w.writeheader()
        w.writerows(cleaned_rows)

    print(f"\nFile {i}: {filename}")
    print(f"  Input:    {total} rows")
    print(f"  Cleaned:  {len(cleaned_rows)} rows -> {out_path}")
    print(f"  Removed:  {bad} rows")

# Write all removed rows
removed_path = os.path.join(OUT_DIR, "removed.csv")
if all_removed:
    all_keys = list(all_removed[0].keys())
    with open(removed_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_removed)

print(f"\nAll removed rows: {len(all_removed)} -> {removed_path}")
print("Done.")
