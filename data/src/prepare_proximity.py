"""
Convert block_hawker_proximity.csv (blk_no, street) -> proximity.csv (block_id, hawker_id)
by matching against hdb_block_clean_YM.csv.
"""

import csv
import os

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BLOCK_CSV = os.path.join(BASE, "clean_data", "hdb_block_clean_YM.csv")
PROX_CSV = os.path.join(BASE, "hawker_geo", "block_hawker_proximity.csv")
OUT_CSV = os.path.join(BASE, "hawker_geo", "proximity.csv")

# Build (blk_no, street) -> block_id mapping
block_map = {}
with open(BLOCK_CSV, newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        key = (row["blk_no"].strip().upper(), row["street"].strip().upper())
        block_map[key] = int(row["block_id"])

print(f"Loaded {len(block_map)} block mappings")

# Convert proximity rows
rows = []
skipped = 0
with open(PROX_CSV, newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        key = (row["blk_no"].strip().upper(), row["street"].strip().upper())
        block_id = block_map.get(key)
        if block_id is None:
            skipped += 1
            continue
        rows.append({
            "block_id": block_id,
            "hawker_id": int(row["nearest_hawker_id"]),
            "distance_m": row["distance_m"],
            "level_id": int(row["proximity_class"]),
        })

with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["block_id", "hawker_id", "distance_m", "level_id"])
    w.writeheader()
    w.writerows(rows)

print(f"proximity.csv: {len(rows)} rows -> {OUT_CSV}")
print(f"Skipped (no block match): {skipped}")
