#!/usr/bin/env python3
"""
03_derive_proximity.py - turn the routed pairs from 02 into every CSV the
schema (schema_hawker.sql) loads, and quantify how much the choice of
distance metric actually matters.

Outputs (all in data/hawker_geo/):
  hdb_hawker_proximity.csv   the M:N relation with walking distance + class
  hdb_block_nearest.csv      one row per block: its nearest centre and that class
  method_comparison.csv      nearest centre by walking vs straight-line vs Manhattan
  resale_transaction.csv     resale transactions joined to block_id
  resale_unmatched.csv       distinct (block, street) that failed to match
  resale_join_report.txt     human-readable join report

Proximity definition (Task 1.1):
  class 1  walk <= 500 m       (~7 min at 4.5 km/h)
  class 2  500 < walk <= 1000 m
  class 3  walk > 1000 m

500 m is the standard walking-catchment radius for a neighbourhood amenity,
1000 m the "10-minute walk" boundary - both defensible without appealing to
the data. A block's own class (what Q4 uses) is the class of its nearest
centre. To build the OneMap variant instead, point WALK_CSV at
hdb_hawker_walk_onemap.csv (produced by 05_reroute_onemap.py).
"""

from __future__ import annotations

import csv
import math
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]          # repo root
OUTDIR = HERE / "data" / "hawker_geo"
HDB_CSV = HERE / "data" / "raw_data" / "hdb.csv"
HAWKER_CSV = HERE / "data" / "hawker_geo" / "hawker_centre.csv"
BLOCKS_CSV = HERE / "data" / "hawker_geo" / "hdb_block.csv"
WALK_CSV = HERE / "data" / "hawker_geo" / "hdb_hawker_walk.csv"
RESALE_CSV = HERE / "data" / "src" / "resale" / "RESALE_CLEANED.csv"

CLASS1_MAX = 500.0
CLASS2_MAX = 1000.0
CLASS_LABELS = {1: "very_near", 2: "near", 3: "far"}


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
        # a resale transaction. Reported separately so the class distribution
        # is not diluted by them.
        is_residential[block_id] = row["residential"].strip() == "Y"

    with HAWKER_CSV.open(encoding="utf-8-sig") as handle:
        hawker_rows = list(csv.DictReader(handle))
    hawker_coords = {
        row["hawker_id"]: (float(row["longitude"]), float(row["latitude"]))
        for row in hawker_rows
    }
    hawker_fresh = {
        row["hawker_id"]: row["no_of_mkt_produce_stalls"] not in ("", None)
                          and int(row["no_of_mkt_produce_stalls"]) > 0
        for row in hawker_rows
    }

    # load the routed pairs
    with WALK_CSV.open(encoding="utf-8") as handle:
        pairs = list(csv.DictReader(handle))
    print(f"[load] {len(pairs)} routed block-hawker pairs")

    # classify and write the relation: exactly the hawker_proximity columns, in order
    relation_path = OUTDIR / "hdb_hawker_proximity.csv"
    with relation_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "block_id", "hawker_id", "walk_distance_m", "walk_duration_s",
            "proximity_class",
        ])
        for row in pairs:
            writer.writerow([
                row["block_id"], row["hawker_id"], row["walk_distance_m"],
                row["walk_duration_s"], classify(float(row["walk_distance_m"])),
            ])

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
    nearest_class_counter: Counter[int] = Counter()
    with nearest_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "block_id", "blk_no", "street", "postal_code", "town",
            "nearest_hawker_id", "nearest_walk_m", "nearest_walk_min",
            "proximity_class", "proximity_label", "nearest_has_fresh_market",
        ])
        meta = {row["block_id"]: row for row in pairs}
        for block_id in sorted(best_walk):
            walk, hawker_id = best_walk[block_id]
            cls = classify(walk)
            nearest_class_counter[cls] += 1
            row = meta[block_id]
            writer.writerow([
                block_id, row["blk_no"], row["street"], row["postal_code"],
                row["town"], hawker_id, round(walk, 1), round(walk / 80, 1),
                cls, CLASS_LABELS[cls],
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
            "nearest_by_walk", "walk_m", "class_by_walk",
            "nearest_by_straight", "straight_m", "class_by_straight",
            "nearest_by_manhattan", "manhattan_m", "class_by_manhattan",
        ])
        writer.writerows(rows_out)

    different_centre_straight = sum(1 for r in rows_out if r[1] != r[4])
    different_centre_manhattan = sum(1 for r in rows_out if r[1] != r[7])
    class_diff_straight = sum(1 for r in rows_out if r[3] != r[6])
    class_diff_manhattan = sum(1 for r in rows_out if r[3] != r[9])

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
    # everything shifts down a class. The fair question is whether there exist
    # straight-line thresholds that reproduce the walking classes well. We find
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
        for i in cut:                      # samples [0, i) predicted class 1
            for j in cut:                  # samples [i, j) predicted class 2
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
    # decides the class), not of every candidate pair
    nearest_ratios = []
    for block_id, (walk_d, hawker_id) in best_walk.items():
        straight_d = straight_to_walk_nearest[(block_id, hawker_id)]
        if straight_d >= 20:   # below that the ratio is dominated by snapping
            nearest_ratios.append(walk_d / straight_d)
    nearest_ratios.sort()

    print("\n" + "=" * 70)
    print("PROXIMITY CLASSES")
    print("=" * 70)
    print(f"class 1  walk <= {CLASS1_MAX:.0f} m : {nearest_class_counter[1]:5d} blocks "
          f"({100 * nearest_class_counter[1] / total_blocks:.1f}%)")
    print(f"class 2  {CLASS1_MAX:.0f}-{CLASS2_MAX:.0f} m     : "
          f"{nearest_class_counter[2]:5d} blocks "
          f"({100 * nearest_class_counter[2] / total_blocks:.1f}%)")
    print(f"class 3  walk >  {CLASS2_MAX:.0f} m : {nearest_class_counter[3]:5d} blocks "
          f"({100 * nearest_class_counter[3] / total_blocks:.1f}%)")
    print(f"total blocks with a hawker       : {total_blocks}")

    residential_counter: Counter[int] = Counter()
    for block_id, (walk_d, _) in best_walk.items():
        if is_residential[block_id]:
            residential_counter[classify(walk_d)] += 1
    residential_total = sum(residential_counter.values())
    print(f"\nrestricted to the {residential_total} residential blocks "
          f"(the ones that can appear in a resale transaction):")
    for cls in (1, 2, 3):
        print(f"    class {cls}: {residential_counter[cls]:5d} "
              f"({100 * residential_counter[cls] / residential_total:.1f}%)")

    print("\n" + "=" * 70)
    print("HOW MUCH DOES THE METRIC MATTER?")
    print("=" * 70)
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
    print(f"    class differs, straight          : {class_diff_straight} blocks "
          f"({100 * class_diff_straight / total_blocks:.1f}%)")
    print(f"    class differs, manhattan         : {class_diff_manhattan} blocks "
          f"({100 * class_diff_manhattan / total_blocks:.1f}%)")
    print()
    print("fair: re-calibrate the cheap metric's thresholds to best fit")
    print(f"    straight-line  best agreement {straight_best[0]:.1f}% "
          f"at {straight_best[1]:.0f} / {straight_best[2]:.0f} m "
          f"-> {100 - straight_best[0]:.1f}% of blocks still misclassified")
    print(f"    manhattan      best agreement {manhattan_best[0]:.1f}% "
          f"at {manhattan_best[1]:.0f} / {manhattan_best[2]:.0f} m "
          f"-> {100 - manhattan_best[0]:.1f}% of blocks still misclassified")

    print(f"\nwrote {relation_path} ({len(pairs)} rows)")
    print(f"wrote {nearest_path} ({total_blocks} rows)")
    print(f"wrote {comparison_path}")


# the HDB property file uses 3-letter contract-town codes; the resale file uses
# full planning-town names. The mapping is 1:1 over all 26 towns and was
# verified block by block against the team's hdb_block_clean_YM.csv.
TOWN_CODE_TO_NAME = {
    "AMK": "ANG MO KIO", "BB": "BUKIT BATOK", "BD": "BEDOK",
    "BH": "BISHAN", "BM": "BUKIT MERAH", "BP": "BUKIT PANJANG",
    "BT": "BUKIT TIMAH", "CCK": "CHOA CHU KANG", "CL": "CLEMENTI",
    "CT": "CENTRAL AREA", "GL": "GEYLANG", "HG": "HOUGANG",
    "JE": "JURONG EAST", "JW": "JURONG WEST", "KWN": "KALLANG/WHAMPOA",
    "MP": "MARINE PARADE", "PG": "PUNGGOL", "PRC": "PASIR RIS",
    "QT": "QUEENSTOWN", "SB": "SEMBAWANG", "SGN": "SERANGOON",
    "SK": "SENGKANG", "TAP": "TAMPINES", "TP": "TOA PAYOH",
    "WL": "WOODLANDS", "YS": "YISHUN",
}

# only abbreviations that are unambiguous in Singapore street names
ABBREV = {
    "AVE": "AVENUE", "AV": "AVENUE", "ST": "STREET", "RD": "ROAD",
    "DR": "DRIVE", "CRES": "CRESCENT", "CL": "CLOSE", "JLN": "JALAN",
    "LOR": "LORONG", "NTH": "NORTH", "STH": "SOUTH", "UPP": "UPPER",
    "BT": "BUKIT", "KG": "KAMPONG", "KPG": "KAMPONG", "TG": "TANJONG",
    "CTRL": "CENTRAL", "IND": "INDUSTRIAL",
}


def clean_block(value: str) -> str:
    """'BLK 123' / 'blk123' / ' 123 ' -> '123'."""
    text = (value or "").strip().upper()
    text = re.sub(r"^(BLK|BLOCK)\b\.?\s*", "", text)
    return text.strip()


def clean_street_exact(value: str) -> str:
    """Conservative normalisation used for stage 1."""
    text = (value or "").upper()
    text = text.replace(".", " ").replace(",", " ")
    return " ".join(text.split())


def clean_street_expanded(value: str) -> str:
    """Stage 2: additionally expand road-type abbreviations."""
    tokens = clean_street_exact(value).split()
    return " ".join(ABBREV.get(token, token) for token in tokens)


def read_blocks(path: str):
    """block_id keyed by (blk_no, street) in both normalisation flavours."""
    exact: dict[tuple[str, str], set[str]] = defaultdict(set)
    expanded: dict[tuple[str, str], set[str]] = defaultdict(set)
    town_of: dict[str, str] = {}

    with open(path, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            block_id = row["block_id"].strip()
            block_no = clean_block(row["blk_no"])
            exact[(block_no, clean_street_exact(row["street"]))].add(block_id)
            expanded[(block_no, clean_street_expanded(row["street"]))].add(block_id)
            town_of[block_id] = TOWN_CODE_TO_NAME.get(row["town"].strip(), "")

    return exact, expanded, town_of


def join_resale() -> None:
    """Match every resale transaction to a block_id.

    Stage 1 exact match on uppercased, whitespace-collapsed (block, street);
    stage 2 retry with road-type abbreviations expanded (a safety net - on the
    current data it recovers nothing, because the residual mismatches are
    demolished blocks, not spelling variants). A candidate is only accepted
    when it is unambiguous; anything still unmatched is reported, never guessed.
    """
    if not os.path.exists(RESALE_CSV):
        print(f"\n[resale] skipped: {RESALE_CSV} not found")
        return

    exact, expanded, town_of = read_blocks(str(BLOCKS_CSV))
    ambiguous_exact = {k: v for k, v in exact.items() if len(v) > 1}
    print(f"\n[resale] {len(town_of)} blocks, {len(exact)} distinct exact keys")
    if ambiguous_exact:
        print(f"[resale][warn] {len(ambiguous_exact)} ambiguous exact keys - rejected")

    matched_exact = matched_expanded = unmatched = 0
    town_mismatch: list[tuple[str, str, str, str]] = []
    miss_counter: Counter = Counter()
    miss_years: dict[tuple[str, str], list[str]] = defaultdict(list)
    miss_town: dict[tuple[str, str], str] = {}

    out_txn = OUTDIR / "resale_transaction.csv"
    out_miss = OUTDIR / "resale_unmatched.csv"
    out_report = OUTDIR / "resale_join_report.txt"

    txn_handle = open(out_txn, "w", newline="", encoding="utf-8")
    writer = csv.writer(txn_handle)
    writer.writerow([
        "resale_id", "block_id", "month", "town", "flat_type",
        "storey_min", "storey_max", "floor_area_sqm", "flat_model",
        "lease_commence_date", "remaining_lease_years", "resale_price",
    ])

    seq = 0
    with open(RESALE_CSV, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            seq += 1
            resale_id = f"R{seq:07d}"
            block_no = clean_block(row["block"])
            street_exact = clean_street_exact(row["street_name"])
            town = row["town"].strip().upper()

            block_id = None
            key = (block_no, street_exact)
            if key in exact and len(exact[key]) == 1:
                block_id = next(iter(exact[key]))
                matched_exact += 1
            else:
                key2 = (block_no, clean_street_expanded(row["street_name"]))
                if key2 in expanded and len(expanded[key2]) == 1:
                    block_id = next(iter(expanded[key2]))
                    matched_expanded += 1

            if block_id is None:
                unmatched += 1
                miss_counter[key] += 1
                miss_years[key].append(row["month"][:4])
                miss_town[key] = town
            else:
                block_town = town_of.get(block_id, "")
                if block_town and town and block_town != town:
                    town_mismatch.append((resale_id, block_id, town, block_town))

            writer.writerow([
                resale_id, block_id or "", row["month"], town, row["flat_type"],
                row["storey_min"], row["storey_max"], row["floor_area_sqm"],
                row["flat_model"], row["lease_commence_date"],
                row["remaining_lease_years"], row["resale_price"],
            ])

    txn_handle.close()

    with open(out_miss, "w", newline="", encoding="utf-8") as handle:
        out = csv.writer(handle)
        out.writerow(["block", "street_name", "town", "txn_rows",
                      "first_year", "last_year"])
        for key, count in miss_counter.most_common():
            years = miss_years[key]
            out.writerow([key[0], key[1], miss_town[key], count,
                          min(years), max(years)])

    # rows the teammate's RESALE_CLEAN.py rejected upstream, for lineage
    removed_csv = HERE / "data" / "src" / "resale" / "RESALE_REMOVED.csv"
    removed_rows = 0
    if os.path.exists(removed_csv):
        with open(removed_csv, newline="", encoding="utf-8-sig") as handle:
            removed_rows = sum(1 for _ in csv.DictReader(handle))

    total = seq
    lines = [
        "RESALE  <->  HDB BLOCK  JOIN REPORT",
        "=" * 46,
        f"transactions read            : {total:,}",
        f"matched (exact)              : {matched_exact:,}"
        f"  ({100 * matched_exact / total:.2f}%)",
        f"matched (abbrev-expanded)    : {matched_expanded:,}"
        f"  ({100 * matched_expanded / total:.2f}%)",
        f"unmatched (block_id = NULL)  : {unmatched:,}"
        f"  ({100 * unmatched / total:.2f}%)",
        f"distinct unmatched blocks    : {len(miss_counter):,}",
        f"rows rejected upstream       : {removed_rows:,}"
        f"  (see data/src/resale/RESALE_REMOVED.csv)",
        f"town inconsistencies         : {len(town_mismatch):,}",
        "",
    ]
    if town_mismatch:
        lines.append("First 10 town inconsistencies (same block+street, different town):")
        lines.extend(
            f"  {rid}  {bid}  resale={rt}  block={bt}"
            for rid, bid, rt, bt in town_mismatch[:10]
        )
        lines.append("")

    lines.append("Unmatched blocks by first year seen (evidence of demolition):")
    by_decade: Counter = Counter()
    for key in miss_counter:
        first = min(miss_years[key])
        by_decade[f"{first[:3]}0s"] += miss_counter[key]
    for decade in sorted(by_decade):
        lines.append(f"  {decade}  {by_decade[decade]:,} rows")
    lines.append("")

    lines.append("Top 15 unmatched blocks by transaction volume:")
    for key, count in miss_counter.most_common(15):
        years = miss_years[key]
        lines.append(f"  {key[0]:<5} {key[1]:<28} {miss_town[key]:<14} "
                     f"{count:>5} rows  {min(years)}-{max(years)}")
    lines.append("")

    lines.append(
        "Interpretation: the unmatched blocks are ones demolished or redeveloped"
        " before the current HDB property register snapshot, so no geocode - and"
        " therefore no proximity class - can exist for them. They are kept with a"
        " NULL block_id; an inner join on hdb_block excludes them from Q4/Q5."
    )

    report = "\n".join(lines)
    with open(out_report, "w", encoding="utf-8") as handle:
        handle.write(report + "\n")

    print(report)
    print(f"\nwrote {out_txn}")
    print(f"wrote {out_miss}")
    print(f"wrote {out_report}")


def main() -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    derive_proximity()
    join_resale()
    return 0


if __name__ == "__main__":
    sys.exit(main())
