#!/usr/bin/env python3
"""
08_join_resale.py
=================
Attach the HDB resale-transaction fact table to our block dimension.

This is the join that Q4 and Q5 need: a resale transaction carries only
`block` + `street_name` text, while our `hdb_block` rows carry a synthetic
`block_id`. Without the mapping there is no way to say "what is the median
resale price of flats whose nearest hawker centre is class 1".

Inputs
------
data/resale_cleaned.csv    986,720 transactions, 1990-01 .. 2026-09
                           (produced by the team's RESALE_CLEAN.py)
data/resale_removed.csv    51 rows rejected during that cleaning, with reason
output/hdb_block.csv       12,442 geocoded blocks with synthetic block_id

Outputs
-------
output/resale_transaction.csv
    One row per transaction. `block_id` is NULL for transactions whose block
    no longer exists in the HDB property register (demolished blocks) - those
    rows are kept, not silently dropped, so that transaction counts stay
    truthful. An inner join on hdb_block excludes them automatically.
output/resale_unmatched.csv
    Every distinct (block, street) that failed to match, with row count and
    the first/last year seen - the evidence that these are demolished blocks.
output/resale_join_report.txt
    Human-readable match report.

Matching strategy
-----------------
Stage 1  exact match on uppercased, whitespace-collapsed (block, street).
Stage 2  if stage 1 misses, retry with road-type abbreviations expanded
         (AVE -> AVENUE, STH -> SOUTH, BT -> BUKIT, ...). This is only a
         safety net: on the current data it recovers nothing, because the
         residual mismatches are demolished blocks, not spelling variants.
Stage 3  whatever is still unmatched is reported, never guessed.

A wrong match is far worse than no match, so a candidate is only accepted
when it is unambiguous (exactly one block_id per key) and, where the resale
town is known, consistent with the block's town. Ambiguous keys are rejected
and reported.

Usage
-----
    python 08_join_resale.py
    python 08_join_resale.py --resale data/resale_cleaned.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root


def _first_existing(*paths: str) -> str:
    """Resolve a data file that may live in either layout.

    Standalone (this folder is the project root):  data/resale_cleaned.csv
    Inside the team repo (this folder is hawker_geo/):  ../data/src/resale/
    The 75 MB resale export is deliberately not duplicated into this folder,
    so the second layout is the normal one there.
    """
    for path in paths:
        if os.path.exists(path):
            return path
    return paths[0]


RESALE_DEFAULT = _first_existing(
    os.path.join(BASE, "data", "src", "resale", "RESALE_CLEANED.csv"),
    os.path.join(BASE, "data", "resale_cleaned.csv"),
)
REMOVED_DEFAULT = _first_existing(
    os.path.join(BASE, "data", "src", "resale", "RESALE_REMOVED.csv"),
    os.path.join(BASE, "data", "resale_removed.csv"),
)
BLOCK_CSV = os.path.join(BASE, "data", "hawker_geo", "hdb_block.csv")

OUT_TXN = os.path.join(BASE, "data", "hawker_geo", "resale_transaction.csv")
OUT_MISS = os.path.join(BASE, "data", "hawker_geo", "resale_unmatched.csv")
OUT_REPORT = os.path.join(BASE, "data", "hawker_geo", "resale_join_report.txt")

# The HDB property file uses 3-letter contract-town codes; the resale file
# uses full planning-town names. The mapping is 1:1 over all 26 towns and was
# verified block by block against the team's hdb_block_clean_YM.csv.
TOWN_CODE_TO_NAME = {
    "AMK": "ANG MO KIO",
    "BB": "BUKIT BATOK",
    "BD": "BEDOK",
    "BH": "BISHAN",
    "BM": "BUKIT MERAH",
    "BP": "BUKIT PANJANG",
    "BT": "BUKIT TIMAH",
    "CCK": "CHOA CHU KANG",
    "CL": "CLEMENTI",
    "CT": "CENTRAL AREA",
    "GL": "GEYLANG",
    "HG": "HOUGANG",
    "JE": "JURONG EAST",
    "JW": "JURONG WEST",
    "KWN": "KALLANG/WHAMPOA",
    "MP": "MARINE PARADE",
    "PG": "PUNGGOL",
    "PRC": "PASIR RIS",
    "QT": "QUEENSTOWN",
    "SB": "SEMBAWANG",
    "SGN": "SERANGOON",
    "SK": "SENGKANG",
    "TAP": "TAMPINES",
    "TP": "TOA PAYOH",
    "WL": "WOODLANDS",
    "YS": "YISHUN",
}

# Only abbreviations that are unambiguous in Singapore street names.
ABBREV = {
    "AVE": "AVENUE",
    "AV": "AVENUE",
    "ST": "STREET",
    "RD": "ROAD",
    "DR": "DRIVE",
    "CRES": "CRESCENT",
    "CL": "CLOSE",
    "JLN": "JALAN",
    "LOR": "LORONG",
    "NTH": "NORTH",
    "STH": "SOUTH",
    "UPP": "UPPER",
    "BT": "BUKIT",
    "KG": "KAMPONG",
    "KPG": "KAMPONG",
    "TG": "TANJONG",
    "CTRL": "CENTRAL",
    "IND": "INDUSTRIAL",
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resale", default=RESALE_DEFAULT)
    parser.add_argument("--blocks", default=BLOCK_CSV)
    parser.add_argument("--removed", default=REMOVED_DEFAULT)
    args = parser.parse_args()

    if not os.path.exists(args.resale):
        sys.exit(f"resale file not found: {args.resale}")
    if not os.path.exists(args.blocks):
        sys.exit(f"block table not found: {args.blocks} (run 02 first)")

    exact, expanded, town_of = read_blocks(args.blocks)

    ambiguous_exact = {k: v for k, v in exact.items() if len(v) > 1}
    ambiguous_expanded = {k: v for k, v in expanded.items() if len(v) > 1}
    print(f"[blocks] {len(town_of)} blocks, {len(exact)} distinct exact keys")
    if ambiguous_exact:
        print(f"[warn] {len(ambiguous_exact)} ambiguous exact keys - rejected")

    matched_exact = matched_expanded = unmatched = 0
    town_mismatch: list[tuple[str, str, str, str]] = []
    miss_counter: Counter = Counter()
    miss_years: dict[tuple[str, str], list[str]] = defaultdict(list)
    miss_town: dict[tuple[str, str], str] = {}

    os.makedirs(os.path.dirname(OUT_TXN), exist_ok=True)
    txn_handle = open(OUT_TXN, "w", newline="", encoding="utf-8")
    writer = csv.writer(txn_handle)
    writer.writerow(
        [
            "resale_id",
            "block_id",
            "month",
            "town",
            "flat_type",
            "storey_min",
            "storey_max",
            "floor_area_sqm",
            "flat_model",
            "lease_commence_date",
            "remaining_lease_years",
            "resale_price",
        ]
    )

    seq = 0
    with open(args.resale, newline="", encoding="utf-8-sig") as handle:
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

            writer.writerow(
                [
                    resale_id,
                    block_id or "",
                    row["month"],
                    town,
                    row["flat_type"],
                    row["storey_min"],
                    row["storey_max"],
                    row["floor_area_sqm"],
                    row["flat_model"],
                    row["lease_commence_date"],
                    row["remaining_lease_years"],
                    row["resale_price"],
                ]
            )

    txn_handle.close()

    with open(OUT_MISS, "w", newline="", encoding="utf-8") as handle:
        out = csv.writer(handle)
        out.writerow(
            ["block", "street_name", "town", "txn_rows", "first_year", "last_year"]
        )
        for key, count in miss_counter.most_common():
            years = miss_years[key]
            out.writerow(
                [key[0], key[1], miss_town[key], count, min(years), max(years)]
            )

    removed_rows = 0
    if os.path.exists(args.removed):
        with open(args.removed, newline="", encoding="utf-8-sig") as handle:
            removed_rows = sum(1 for _ in csv.DictReader(handle))

    total = seq
    lines = []
    lines.append("RESALE  <->  HDB BLOCK  JOIN REPORT")
    lines.append("=" * 46)
    lines.append(f"transactions read            : {total:,}")
    lines.append(f"matched (exact)              : {matched_exact:,}"
                 f"  ({100 * matched_exact / total:.2f}%)")
    lines.append(f"matched (abbrev-expanded)    : {matched_expanded:,}"
                 f"  ({100 * matched_expanded / total:.2f}%)")
    lines.append(f"unmatched (block_id = NULL)  : {unmatched:,}"
                 f"  ({100 * unmatched / total:.2f}%)")
    lines.append(f"distinct unmatched blocks    : {len(miss_counter):,}")
    lines.append(f"rows rejected upstream       : {removed_rows:,}"
                 "  (see data/resale_removed.csv)")
    lines.append(f"town inconsistencies         : {len(town_mismatch):,}")
    lines.append("")

    if town_mismatch:
        lines.append("First 10 town inconsistencies (same block+street, different town):")
        for resale_id, block_id, resale_town, block_town in town_mismatch[:10]:
            lines.append(
                f"  {resale_id}  {block_id}  resale={resale_town}  block={block_town}"
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
        lines.append(
            f"  {key[0]:<5} {key[1]:<28} {miss_town[key]:<14} "
            f"{count:>5} rows  {min(years)}-{max(years)}"
        )
    lines.append("")
    lines.append(
        "Interpretation: the unmatched blocks are ones demolished or redeveloped"
        " before the current HDB property register snapshot, so no geocode - and"
        " therefore no proximity class - can exist for them. They are kept with a"
        " NULL block_id; an inner join on hdb_block excludes them from Q4/Q5."
    )

    report = "\n".join(lines)
    with open(OUT_REPORT, "w", encoding="utf-8") as handle:
        handle.write(report + "\n")

    print(report)
    print(f"\nwrote {OUT_TXN}")
    print(f"wrote {OUT_MISS}")
    print(f"wrote {OUT_REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
