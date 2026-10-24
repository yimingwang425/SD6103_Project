"""
Step 2 of 2: Split Cleaned Resale Data into Database Tables
============================================================

Purpose:
    Transform cleaned resale CSVs into per-table CSVs matching the ER model.
    Each cleaned file is split into two tables:
      - hdb_flat: unique flat types (one row per unique combination of
        block_id + flat_type + storey_min + storey_max + floor_area_sqm + flat_model)
      - resale_transaction: one row per transaction, linked to flat_id

    The output supports incremental loading: each file's hdb_flat CSV contains
    only NEW flats not seen in previous files.

Input:
    - data/src/resale/cleaned/file{1..5}_cleaned.csv  (from clean.py)
    - data/clean_data/hdb_block_clean_YM.csv          (from HDB block team)

Output:
    data/src/resale/incremental/
      file1_hdb_flat.csv, file1_resale_transaction.csv
      file2_hdb_flat.csv, file2_resale_transaction.csv
      ...
      file5_hdb_flat.csv, file5_resale_transaction.csv
      skipped.csv  (rows that could not be matched to a block_id)

Dependencies:
    - clean.py must be run first to produce cleaned CSVs
    - hdb_block_clean_YM.csv must be available (produced by HDB block team)
      This file provides the (blk_no, street) -> block_id mapping needed
      to link flats to blocks via foreign key.

Key design decisions:
    1. Flat identity: a flat is defined as the unique combination of
       (block_id, flat_type, storey_min, storey_max, floor_area_sqm, flat_model).
       This represents a flat TYPE within a storey band, not a physical unit,
       because the raw data does not contain unit-level identifiers.

    2. Incremental flat_id: flat_ids are assigned globally across all 5 files
       in order. If a flat combo first appears in file 1 and reappears in file 3,
       file 3's hdb_flat CSV will NOT contain it (no duplicate), but file 3's
       resale_transaction CSV will reference the existing flat_id.

    3. Unmatched blocks: rows whose (block, street_name) has no matching
       block_id are skipped and logged to skipped.csv. These are typically
       older or demolished blocks absent from the HDB geolocation dataset.

Run:
    python data/src/resale/split.py
"""

import csv
import os

# ── Paths ──
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLEANED_DIR = os.path.join(BASE, "src", "resale", "cleaned")
BLOCK_CSV = os.path.join(BASE, "clean_data", "hdb_block_clean_YM.csv")
OUT_DIR = os.path.join(BASE, "src", "resale", "incremental")
os.makedirs(OUT_DIR, exist_ok=True)

NUM_FILES = 5

FLAT_FIELDS = [
    "flat_id", "block_id", "flat_type",
    "storey_min", "storey_max", "floor_area_sqm", "flat_model",
]
TXN_FIELDS = [
    "transaction_id", "flat_id",
    "transaction_year", "transaction_month",
    "remaining_lease_years", "resale_price",
]


# Load block_id mapping
block_map = {}  # (blk_no, street) -> block_id
with open(BLOCK_CSV, newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        key = (row["blk_no"].strip().lower(), row["street"].strip().lower())
        block_map[key] = int(row["block_id"])

print(f"Loaded {len(block_map)} block mappings from hdb_block_clean_YM.csv")

# Process each cleaned file
flat_map = {}       # flat_key -> flat_id (cumulative across files)
next_flat_id = 1
next_txn_id = 1
all_skipped = []

for i in range(1, NUM_FILES + 1):
    cleaned_path = os.path.join(CLEANED_DIR, f"file{i}_cleaned.csv")
    new_flats = []
    txn_rows = []
    skipped = 0
    total = 0

    with open(cleaned_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            total += 1

            # Match (block, street_name) -> block_id
            block_key = (row["block"].strip().lower(), row["street_name"].strip().lower())
            block_id = block_map.get(block_key)
            if block_id is None:
                row["source_file"] = f"file{i}"
                all_skipped.append(row)
                skipped += 1
                continue

            # Determine flat_id
            flat_key = (
                block_id,
                row["flat_type"],
                int(row["storey_min"]),
                int(row["storey_max"]),
                row["floor_area_sqm"],
                row["flat_model"],
            )

            if flat_key not in flat_map:
                flat_map[flat_key] = next_flat_id
                new_flats.append({
                    "flat_id": next_flat_id,
                    "block_id": block_id,
                    "flat_type": row["flat_type"],
                    "storey_min": int(row["storey_min"]),
                    "storey_max": int(row["storey_max"]),
                    "floor_area_sqm": row["floor_area_sqm"],
                    "flat_model": row["flat_model"],
                })
                next_flat_id += 1

            flat_id = flat_map[flat_key]
            year, month = row["month"].split("-")

            txn_rows.append({
                "transaction_id": next_txn_id,
                "flat_id": flat_id,
                "transaction_year": int(year),
                "transaction_month": int(month),
                "remaining_lease_years": int(row["remaining_lease_years"]),
                "resale_price": int(row["resale_price"]),
            })
            next_txn_id += 1

    # Write per-file CSVs
    flat_path = os.path.join(OUT_DIR, f"file{i}_hdb_flat.csv")
    txn_path = os.path.join(OUT_DIR, f"file{i}_resale_transaction.csv")

    with open(flat_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FLAT_FIELDS)
        w.writeheader()
        w.writerows(new_flats)

    with open(txn_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TXN_FIELDS)
        w.writeheader()
        w.writerows(txn_rows)

    print(f"\nFile {i}:")
    print(f"  Cleaned rows:   {total}")
    print(f"  New flats:      {len(new_flats)} -> {flat_path}")
    print(f"  Transactions:   {len(txn_rows)} -> {txn_path}")
    print(f"  Skipped (no block_id): {skipped}")

# Write all skipped rows
skipped_path = os.path.join(OUT_DIR, "skipped.csv")
if all_skipped:
    with open(skipped_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(all_skipped[0].keys()))
        w.writeheader()
        w.writerows(all_skipped)

print(f"\n{'=' * 60}")
print(f"Total flats:        {len(flat_map)}")
print(f"Total transactions: {next_txn_id - 1}")
print(f"Total skipped:      {len(all_skipped)} -> {skipped_path}")
print(f"Output directory:   {OUT_DIR}")
