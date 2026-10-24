"""
Extract columns needed for hdb_block table from hdb_block_clean_YM.csv.
Output: hdb_block_import.csv (block_id, town_id, blk_no, street, year_completed, lat, lng, postal)
"""

import csv
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INPUT = os.path.join(BASE, "clean_data", "hdb_block_clean_YM.csv")
OUTPUT = os.path.join(BASE, "clean_data", "hdb_block_import.csv")

FIELDS = ["block_id", "town_id", "blk_no", "street", "year_completed", "lat", "lng", "postal"]

with open(INPUT, newline="", encoding="utf-8") as fin, \
     open(OUTPUT, "w", newline="", encoding="utf-8") as fout:
    reader = csv.DictReader(fin)
    writer = csv.DictWriter(fout, fieldnames=FIELDS)
    writer.writeheader()
    count = 0
    for row in reader:
        writer.writerow({f: row[f] for f in FIELDS})
        count += 1

print(f"hdb_block_import.csv: {count} rows -> {OUTPUT}")
