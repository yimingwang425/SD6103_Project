#!/usr/bin/env python3
"""Hawker Geo Pipeline — standalone, deliverable-grade ETL for the hawker layer.

This script is intentionally decoupled from etl.py: it reads the two raw
inputs directly (hawker geocodes + HDB block geocodes) and produces a cleaned
hawker dimension table plus a block -> nearest-hawker distance table.

INPUTS  (relative to BASE)
  raw/hawker_geo.csv   columns: longitude, latitude, NAME, ADDRESS_MYENV
  raw/hdb.csv          columns: blk_no, street, lat, lng, ... (block geocodes)

OUTPUTS (under data/hawker_geo/)
  hawker_centres_clean.csv     cleaned hawker dimension (1 row per hawker)
  block_hawker_proximity.csv   every geocoded block + nearest hawker + distance

Distance model
  Great-circle (haversine) distance in metres. This is a transparent, free,
  reproducible proxy. If true walking/road distance is later required, swap
  compute_proximity() for an OSRM/GraphHopper call — the output schema is
  unchanged.

Proximity classes (kept identical to the project ER model)
  class 1 = NEAR    : distance <= 800 m
  class 2 = MEDIUM  : 800 m < distance <= 2000 m
  class 3 = FAR     : distance > 2000 m

Run:
  python scripts/hawker_geo_pipeline.py
"""

import csv
import math
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
BASE = Path(__file__).resolve().parents[1]
RAW = BASE / "raw"
OUT_DIR = BASE / "data" / "hawker_geo"

HAWKER_CSV = RAW / "hawker_geo.csv"
HDB_CSV = RAW / "hdb.csv"

EARTH_R = 6371000.0  # metres

# Singapore bounding box — used to reject obviously broken coordinates.
SG_LAT_MIN, SG_LAT_MAX = 1.20, 1.50
SG_LON_MIN, SG_LON_MAX = 103.55, 104.10

# Proximity thresholds (metres)
NEAR_MAX = 800.0
MEDIUM_MAX = 2000.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres between two lat/lon points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return 2 * EARTH_R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def norm(s):
    """Normalise a string key: uppercase, collapse whitespace, strip."""
    return re.sub(r"\s+", " ", str(s).strip().upper())


def proximity_class(d):
    if d <= NEAR_MAX:
        return 1, "NEAR"
    if d <= MEDIUM_MAX:
        return 2, "MEDIUM"
    return 3, "FAR"


# ---------------------------------------------------------------------------
# Stage 1 — clean the hawker table
# ---------------------------------------------------------------------------
def load_and_clean_hawkers(path):
    """Read raw hawker geocodes, validate, dedupe, derive flags.

    Returns (rows, report) where rows is a list of dicts with contiguous
    hawker_id, and report is a dict of data-quality counters.
    """
    report = {
        "rows_read": 0,
        "dropped_missing_coords": 0,
        "dropped_out_of_bounds": 0,
        "dropped_empty_name": 0,
        "duplicates_removed": 0,
        "has_fresh_market": 0,
    }
    seen = {}            # (norm_name, round_lat, round_lon) -> hawker_id
    rows = []
    next_id = 1

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for src_row, raw in enumerate(reader, start=2):  # +2: header + 1-based
            report["rows_read"] += 1
            name = (raw.get("NAME") or "").strip()
            addr = (raw.get("ADDRESS_MYENV") or "").strip()

            if not name:
                report["dropped_empty_name"] += 1
                continue

            try:
                lat = float(raw["latitude"])
                lon = float(raw["longitude"])
            except (KeyError, ValueError, TypeError):
                report["dropped_missing_coords"] += 1
                continue

            if not (SG_LAT_MIN <= lat <= SG_LAT_MAX
                    and SG_LON_MIN <= lon <= SG_LON_MAX):
                report["dropped_out_of_bounds"] += 1
                continue

            key = (norm(name), round(lat, 5), round(lon, 5))
            if key in seen:
                report["duplicates_removed"] += 1
                continue

            has_fresh = "MARKET" in name.upper() or "PASAR" in name.upper()
            if has_fresh:
                report["has_fresh_market"] += 1

            hid = next_id
            next_id += 1
            seen[key] = hid
            rows.append({
                "hawker_id": hid,
                "source_row": src_row,
                "name": name,
                "address": addr,
                "latitude": round(lat, 8),
                "longitude": round(lon, 8),
                "has_fresh_market": int(has_fresh),
            })
    return rows, report


# ---------------------------------------------------------------------------
# Stage 2 — load block geocodes (deduped by blk_no + street)
# ---------------------------------------------------------------------------
def load_blocks(path):
    """Return list of unique geocoded blocks: (blk_no, street, town, lat, lon)."""
    blocks = []
    seen = set()
    dropped = 0
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            blk = (raw.get("blk_no") or "").strip().upper()
            street = norm(raw.get("street", ""))
            town = (raw.get("bldg_contract_town") or "").strip().upper()
            try:
                lat = float(raw["lat"])
                lon = float(raw["lng"])
            except (KeyError, ValueError, TypeError):
                dropped += 1
                continue
            if not blk or not street:
                dropped += 1
                continue
            if not (SG_LAT_MIN <= lat <= SG_LAT_MAX
                    and SG_LON_MIN <= lon <= SG_LON_MAX):
                dropped += 1
                continue
            key = (blk, street)
            if key in seen:
                continue  # duplicate block entry
            seen.add(key)
            blocks.append({
                "blk_no": blk,
                "street": street,
                "town": town,
                "latitude": lat,
                "longitude": lon,
            })
    return blocks, dropped


# ---------------------------------------------------------------------------
# Stage 3 — compute block -> nearest hawker distance
# ---------------------------------------------------------------------------
def compute_proximity_for_blocks(blocks, hawkers):
    """For each block, find the nearest hawker and the haversine distance.

    Returns a list of result dicts. Complexity: |blocks| x |hawkers| — at
    project scale (~12k blocks x ~120 hawkers) this is ~1.5M cheap calls and
    runs in well under a second locally.
    """
    results = []
    for b in blocks:
        best_id = None
        best_name = None
        best_d = float("inf")
        for h in hawkers:
            d = haversine(b["latitude"], b["longitude"],
                          h["latitude"], h["longitude"])
            if d < best_d:
                best_d = d
                best_id = h["hawker_id"]
                best_name = h["name"]
        cls, label = proximity_class(best_d)
        results.append({
            "blk_no": b["blk_no"],
            "street": b["street"],
            "town": b["town"],
            "block_lat": round(b["latitude"], 8),
            "block_lng": round(b["longitude"], 8),
            "nearest_hawker_id": best_id,
            "nearest_hawker_name": best_name,
            "distance_m": round(best_d, 2),
            "proximity_class": cls,
            "proximity_label": label,
        })
    return results


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------
def write_csv(path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def main():
    if not HAWKER_CSV.exists():
        sys.exit(f"[FATAL] hawker file not found: {HAWKER_CSV}")
    if not HDB_CSV.exists():
        sys.exit(f"[FATAL] hdb file not found: {HDB_CSV}")

    print("=" * 60)
    print("Hawker Geo Pipeline")
    print("=" * 60)

    # Stage 1
    hawkers, rep = load_and_clean_hawkers(HAWKER_CSV)
    print(f"\n[1] Hawker cleaning")
    print(f"    read            : {rep['rows_read']}")
    print(f"    kept (clean)    : {len(hawkers)}")
    print(f"    dropped no-name : {rep['dropped_empty_name']}")
    print(f"    dropped coords  : {rep['dropped_missing_coords']}")
    print(f"    dropped oob     : {rep['dropped_out_of_bounds']}")
    print(f"    duplicates      : {rep['duplicates_removed']}")
    print(f"    with market     : {rep['has_fresh_market']}")

    hawker_fields = ["hawker_id", "source_row", "name", "address",
                     "latitude", "longitude", "has_fresh_market"]
    write_csv(OUT_DIR / "hawker_centres_clean.csv", hawker_fields, hawkers)
    print(f"    -> {OUT_DIR / 'hawker_centres_clean.csv'}")

    # Stage 2
    blocks, dropped_blocks = load_blocks(HDB_CSV)
    print(f"\n[2] Block geocodes")
    print(f"    unique blocks   : {len(blocks)}")
    print(f"    dropped/invalid : {dropped_blocks}")

    # Stage 3
    results = compute_proximity_for_blocks(blocks, hawkers)
    print(f"\n[3] Proximity (block -> nearest hawker)")
    cls_counts = {1: 0, 2: 0, 3: 0}
    for r in results:
        cls_counts[r["proximity_class"]] += 1
    print(f"    NEAR (<=800m)    : {cls_counts[1]}")
    print(f"    MEDIUM (<=2km)   : {cls_counts[2]}")
    print(f"    FAR (>2km)       : {cls_counts[3]}")

    prox_fields = ["blk_no", "street", "town", "block_lat", "block_lng",
                   "nearest_hawker_id", "nearest_hawker_name",
                   "distance_m", "proximity_class", "proximity_label"]
    write_csv(OUT_DIR / "block_hawker_proximity.csv", prox_fields, results)
    print(f"    -> {OUT_DIR / 'block_hawker_proximity.csv'}")

    print("\nDone.")


if __name__ == "__main__":
    main()
