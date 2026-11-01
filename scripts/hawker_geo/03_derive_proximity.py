#!/usr/bin/env python3
"""
03_derive_proximity.py - derive data/clean_data/proximity.csv +
proximity_level.csv from the routed pairs (walking bands 1 <=500 m,
2 <=1000 m, 3 >1000 m; natural keys, sp06-sp08 resolve the ids) and
compare the three distance metrics.
"""

from __future__ import annotations

import csv
import math
import statistics
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]          # repo root
CLEAN_DIR = HERE / "data" / "clean_data"
OUTDIR = HERE / "data" / "hawker_geo"
HDB_CSV = HERE / "data" / "raw_data" / "hdb.csv"
HAWKER_CSV = CLEAN_DIR / "hawker_centre.csv"
# OneMap walk routes (04_validate_distances.py --full); the OSRM variant
# written by 02_walk_distances.py is hdb_hawker_walk.csv.
WALK_CSV = OUTDIR / "hdb_hawker_walk_onemap.csv"

CLASS1_MAX = 500.0
CLASS2_MAX = 1000.0
LEVEL_CATEGORY = {1: "NEAR", 2: "MID", 3: "FAR"}

def manhattan_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Axis-aligned distance using a local equirectangular projection."""
    mean_lat = math.radians((lat1 + lat2) / 2)
    dx = abs(lon2 - lon1) * 111_320 * math.cos(mean_lat)
    dy = abs(lat2 - lat1) * 110_574
    return dx + dy


def classify(distance_m: float) -> int:
    if distance_m <= CLASS1_MAX:
        return 1
    if distance_m <= CLASS2_MAX:
        return 2
    return 3


def load_hawker_names() -> dict[str, str]:
    """legacy 'H001' -> hawker_name. The clean stage writes
    hawker_centre.csv in geo-file order, which is exactly the order the
    Hxxx numbering was assigned in."""
    with HAWKER_CSV.open(encoding="utf-8-sig") as handle:
        return {f"H{index + 1:03d}": row["hawker_name"]
                for index, row in enumerate(csv.DictReader(handle))}


def derive_proximity() -> None:
    # coordinates for the comparison metrics
    with HDB_CSV.open(encoding="utf-8-sig") as handle:
        hdb_rows = list(csv.DictReader(handle))
    coords = {}
    is_residential = {}
    for index, row in enumerate(hdb_rows):
        block_id = f"B{index + 1:05d}"
        coords[block_id] = (float(row["lng"]), float(row["lat"]))
        # residential == 'N' is exactly the set of blocks with zero dwelling
        # units (2,261 of 12,442): pure commercial blocks that can never carry
        # a resale transaction. Reported separately so the level distribution
        # is not diluted by them.
        is_residential[block_id] = row["residential"].strip() == "Y"

    with HAWKER_CSV.open(encoding="utf-8-sig") as handle:
        hawker_rows = list(csv.DictReader(handle))
    # keyed by the legacy Hxxx id: that is what the routing pipeline speaks
    hawker_coords = {
        f"H{index + 1:03d}": (float(row["longitude"]), float(row["latitude"]))
        for index, row in enumerate(hawker_rows)
    }
    hawker_fresh = {
        f"H{index + 1:03d}": row["no_of_mkt_produce_stalls"] not in ("", None)
                             and int(row["no_of_mkt_produce_stalls"]) > 0
        for index, row in enumerate(hawker_rows)
    }
    hawker_name = load_hawker_names()

    # load the routed pairs. hdb_hawker_walk_onemap.csv is gitignored on
    # purpose: it rebuilds in seconds from the committed onemap_full.jsonl,
    # with no token and no network calls.
    if not WALK_CSV.exists():
        sys.exit(
            f"missing input: {WALK_CSV}\n"
            "This file is gitignored because it rebuilds offline from the\n"
            "committed routing cache. Run:\n"
            "    python scripts/hawker_geo/04_validate_distances.py "
            "--engine onemap --full --merge-only\n"
        )
    with WALK_CSV.open(encoding="utf-8") as handle:
        pairs = list(csv.DictReader(handle))
    print(f"[load] {len(pairs)} routed block-hawker pairs")

    # write the relation: natural keys, sp08 resolves the ids; each pair
    # carries the walking band it falls in
    relation_path = CLEAN_DIR / "proximity.csv"
    with relation_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["blk_no", "street", "hawker_name", "level_id"])
        for row in pairs:
            writer.writerow([
                row["blk_no"], row["street"], hawker_name[row["hawker_id"]],
                classify(float(row["walk_distance_m"])),
            ])

    # the three level bands (min, max]: a boundary distance belongs to the
    # lower level - one pair in the data sits exactly on 1000.0 m
    level_path = CLEAN_DIR / "proximity_level.csv"
    with level_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["level_id", "category", "distance_min_m", "distance_max_m"])
        writer.writerow([1, LEVEL_CATEGORY[1], 0, int(CLASS1_MAX)])
        writer.writerow([2, LEVEL_CATEGORY[2], int(CLASS1_MAX), int(CLASS2_MAX)])
        writer.writerow([3, LEVEL_CATEGORY[3], int(CLASS2_MAX), ""])

    # nearest hawker per block, by each of the three metrics
    best_walk: dict[str, tuple[float, str]] = {}
    best_straight: dict[str, tuple[float, str]] = {}
    best_manhattan: dict[str, tuple[float, str]] = {}
    for row in pairs:
        block_id, hawker_id = row["block_id"], row["hawker_id"]
        walk = float(row["walk_distance_m"])
        block_lon, block_lat = coords[block_id]
        hawker_lon, hawker_lat = hawker_coords[hawker_id]
        straight = float(row["straight_line_m"])
        manhattan = manhattan_m(block_lon, block_lat, hawker_lon, hawker_lat)

        if block_id not in best_walk or walk < best_walk[block_id][0]:
            best_walk[block_id] = (walk, hawker_id)
        if block_id not in best_straight or straight < best_straight[block_id][0]:
            best_straight[block_id] = (straight, hawker_id)
        if block_id not in best_manhattan or manhattan < best_manhattan[block_id][0]:
            best_manhattan[block_id] = (manhattan, hawker_id)

    nearest_path = OUTDIR / "hdb_block_nearest.csv"
    nearest_level_counter: Counter[int] = Counter()
    with nearest_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "block_id", "blk_no", "street", "postal_code", "town",
            "nearest_hawker_id", "nearest_walk_m", "nearest_walk_min",
            "proximity_level", "level_category", "nearest_has_fresh_market",
        ])
        meta = {row["block_id"]: row for row in pairs}
        for block_id in sorted(best_walk):
            walk, hawker_id = best_walk[block_id]
            level = classify(walk)
            nearest_level_counter[level] += 1
            row = meta[block_id]
            writer.writerow([
                block_id, row["blk_no"], row["street"], row["postal_code"],
                row["town"], hawker_id, round(walk, 1), round(walk / 80, 1),
                level, LEVEL_CATEGORY[level],
                "true" if hawker_fresh[hawker_id] else "false",
            ])

    # how much does the metric matter?
    comparison_path = OUTDIR / "method_comparison.csv"
    rows_out = []
    for block_id in sorted(best_walk):
        walk_d, walk_h = best_walk[block_id]
        straight_d, straight_h = best_straight[block_id]
        manhattan_d, manhattan_h = best_manhattan[block_id]
        rows_out.append([
            block_id,
            walk_h, round(walk_d, 1), classify(walk_d),
            straight_h, round(straight_d, 1), classify(straight_d),
            manhattan_h, round(manhattan_d, 1), classify(manhattan_d),
        ])
    with comparison_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "block_id",
            "nearest_by_walk", "walk_m", "level_by_walk",
            "nearest_by_straight", "straight_m", "level_by_straight",
            "nearest_by_manhattan", "manhattan_m", "level_by_manhattan",
        ])
        writer.writerows(rows_out)

    different_centre_straight = sum(1 for r in rows_out if r[1] != r[4])
    different_centre_manhattan = sum(1 for r in rows_out if r[1] != r[7])
    level_diff_straight = sum(1 for r in rows_out if r[3] != r[6])
    level_diff_manhattan = sum(1 for r in rows_out if r[3] != r[9])

    # Ratios are only meaningful once the straight-line distance is well above
    # the routing snap tolerance. Some hawker centres sit inside the very HDB
    # block they serve (straight line ~0 m, walk 60-90 m); the ratio there is
    # pure noise, so such pairs are excluded from the ratio statistics.
    RATIO_MIN_M = 20.0
    ratios = [float(r["walk_distance_m"]) / float(r["straight_line_m"])
              for r in pairs if float(r["straight_line_m"]) >= RATIO_MIN_M]
    manhattan_ratios = []
    for row in pairs:
        block_lon, block_lat = coords[row["block_id"]]
        hawker_lon, hawker_lat = hawker_coords[row["hawker_id"]]
        manhattan = manhattan_m(block_lon, block_lat, hawker_lon, hawker_lat)
        if manhattan >= RATIO_MIN_M:
            manhattan_ratios.append(float(row["walk_distance_m"]) / manhattan)

    total_blocks = len(rows_out)

    # Is straight-line distance salvageable with better thresholds?
    #
    # Applying the *walking* thresholds (500/1000) to a straight-line distance
    # is not a fair test: straight lines are systematically shorter, so almost
    # everything shifts down a level. The fair question is whether there exist
    # straight-line thresholds that reproduce the walking levels well. We find
    # the optimal pair by exhaustive search over candidate cut points, which is
    # cheap because the correct-count can be read off prefix sums.
    straight_to_walk_nearest = {
        (row["block_id"], row["hawker_id"]): float(row["straight_line_m"])
        for row in pairs
    }
    manhattan_to_walk_nearest = {}
    for row in pairs:
        block_lon, block_lat = coords[row["block_id"]]
        hawker_lon, hawker_lat = hawker_coords[row["hawker_id"]]
        manhattan_to_walk_nearest[(row["block_id"], row["hawker_id"])] = \
            manhattan_m(block_lon, block_lat, hawker_lon, hawker_lat)

    def best_threshold_agreement(metric_of) -> tuple[float, float, float]:
        """Return (best accuracy %, t1, t2) for a 3-class split on `metric_of`."""
        samples = []
        for block_id, (walk_d, hawker_id) in best_walk.items():
            samples.append((metric_of[(block_id, hawker_id)], classify(walk_d)))
        samples.sort()
        n = len(samples)
        # prefix counts of the true class among the first i sorted samples
        c1, c2, c3 = [0], [0], [0]
        for _, truth in samples:
            c1.append(c1[-1] + (truth == 1))
            c2.append(c2[-1] + (truth == 2))
            c3.append(c3[-1] + (truth == 3))
        step = max(1, n // 200)
        cut = list(range(0, n + 1, step))
        if cut[-1] != n:
            cut.append(n)
        best_correct = -1
        best_t1 = best_t2 = 0.0
        for i in cut:                      # samples [0, i) predicted level 1
            for j in cut:                  # samples [i, j) predicted level 2
                if j < i:
                    continue
                correct = c1[i] + (c2[j] - c2[i]) + (c3[n] - c3[j])
                if correct > best_correct:
                    best_correct = correct
                    best_t1 = samples[i - 1][0] if i > 0 else 0.0
                    best_t2 = samples[j - 1][0] if j > 0 else 0.0
        return 100 * best_correct / n, best_t1, best_t2

    straight_best = best_threshold_agreement(straight_to_walk_nearest)
    manhattan_best = best_threshold_agreement(manhattan_to_walk_nearest)

    # detour factor of each block's nearest pair (the number that actually
    # decides the level), not of every candidate pair
    nearest_ratios = []
    for block_id, (walk_d, hawker_id) in best_walk.items():
        straight_d = straight_to_walk_nearest[(block_id, hawker_id)]
        if straight_d >= 20:   # below that the ratio is dominated by snapping
            nearest_ratios.append(walk_d / straight_d)
    nearest_ratios.sort()

    print("\nPROXIMITY LEVELS")
    print(f"level 1  walk <= {CLASS1_MAX:.0f} m : {nearest_level_counter[1]:5d} blocks "
          f"({100 * nearest_level_counter[1] / total_blocks:.1f}%)")
    print(f"level 2  {CLASS1_MAX:.0f}-{CLASS2_MAX:.0f} m     : "
          f"{nearest_level_counter[2]:5d} blocks "
          f"({100 * nearest_level_counter[2] / total_blocks:.1f}%)")
    print(f"level 3  walk >  {CLASS2_MAX:.0f} m : {nearest_level_counter[3]:5d} blocks "
          f"({100 * nearest_level_counter[3] / total_blocks:.1f}%)")
    print(f"total blocks with a hawker       : {total_blocks}")

    residential_counter: Counter[int] = Counter()
    for block_id, (walk_d, _) in best_walk.items():
        if is_residential[block_id]:
            residential_counter[classify(walk_d)] += 1
    residential_total = sum(residential_counter.values())
    print(f"\nrestricted to the {residential_total} residential blocks "
          f"(the ones that can appear in a resale transaction):")
    for level in (1, 2, 3):
        print(f"    level {level}: {residential_counter[level]:5d} "
              f"({100 * residential_counter[level] / residential_total:.1f}%)")

    print("\nHOW MUCH DOES THE METRIC MATTER?")
    print(f"pairs considered                 : {len(pairs)}")
    print(f"walk / straight-line   median    : {statistics.median(ratios):.3f}  "
          f"(mean {statistics.mean(ratios):.3f})")
    print(f"walk / manhattan       median    : "
          f"{statistics.median(manhattan_ratios):.3f}  "
          f"(mean {statistics.mean(manhattan_ratios):.3f})")
    print(f"pairs where walk > 1.5x straight : "
          f"{sum(1 for r in ratios if r > 1.5)} "
          f"({100 * sum(1 for r in ratios if r > 1.5) / len(ratios):.1f}%)")
    print()
    print("detour factor of each block's nearest centre:")
    print(f"    p5 / p50 / p95               : "
          f"{nearest_ratios[len(nearest_ratios) // 20]:.2f} / "
          f"{statistics.median(nearest_ratios):.2f} / "
          f"{nearest_ratios[int(len(nearest_ratios) * 0.95)]:.2f}")
    print(f"    blocks above 2.0x            : "
          f"{sum(1 for r in nearest_ratios if r > 2.0)} "
          f"({100 * sum(1 for r in nearest_ratios if r > 2.0) / len(nearest_ratios):.1f}%)")
    print()
    print("naive: reuse the walking thresholds on the cheap metric")
    print(f"    nearest centre differs, straight : {different_centre_straight} blocks "
          f"({100 * different_centre_straight / total_blocks:.1f}%)")
    print(f"    nearest centre differs, manhattan: {different_centre_manhattan} blocks "
          f"({100 * different_centre_manhattan / total_blocks:.1f}%)")
    print(f"    level differs, straight          : {level_diff_straight} blocks "
          f"({100 * level_diff_straight / total_blocks:.1f}%)")
    print(f"    level differs, manhattan         : {level_diff_manhattan} blocks "
          f"({100 * level_diff_manhattan / total_blocks:.1f}%)")
    print()
    print("fair: re-calibrate the cheap metric's thresholds to best fit")
    print(f"    straight-line  best agreement {straight_best[0]:.1f}% "
          f"at {straight_best[1]:.0f} / {straight_best[2]:.0f} m "
          f"-> {100 - straight_best[0]:.1f}% of blocks still misclassified")
    print(f"    manhattan      best agreement {manhattan_best[0]:.1f}% "
          f"at {manhattan_best[1]:.0f} / {manhattan_best[2]:.0f} m "
          f"-> {100 - manhattan_best[0]:.1f}% of blocks still misclassified")

    print(f"\nwrote {relation_path} ({len(pairs)} rows)")
    print(f"wrote {level_path}")
    print(f"wrote {nearest_path} ({total_blocks} rows)")
    print(f"wrote {comparison_path}")

def main() -> int:
    derive_proximity()
    return 0


if __name__ == "__main__":
    sys.exit(main())
