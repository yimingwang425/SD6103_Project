#!/usr/bin/env python3
"""Re-implement Q4/Q5 in plain Python from data/clean_data/ so the numbers
in queries_hawker.sql can be checked without a PostgreSQL server.
"""
from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
CLEAN = HERE / "data" / "clean_data"
RESALE_DIR = CLEAN / "resale"

def read(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))

blocks = read(CLEAN / "hdb_block_clean.csv")
town_of = {(b["blk_no"], b["street_name"] if "street_name" in b else b["street"]):
           b["town_name"] for b in blocks}
block_keys = set(town_of)

hawker = read(CLEAN / "hawker_centre.csv")
prox = read(CLEAN / "proximity.csv")

resale = []
for i in range(1, 6):
    resale.extend(read(RESALE_DIR / f"file{i}_cleaned.csv"))
print(f"[load] {len(resale):,} transactions, {len(block_keys):,} blocks, "
      f"{len(prox):,} proximity pairs")

prox = [p for p in prox if (p["blk_no"], p["street"]) in block_keys]
print(f"[load] {len(prox):,} pairs on blocks present in hdb_block")

level: dict[tuple[str, str], int] = {}
for p in prox:
    key = (p["blk_no"], p["street"])
    lv = int(p["level_id"])
    if key not in level or lv < level[key]:
        level[key] = lv

priced = defaultdict(lambda: defaultdict(list))  # town -> level -> prices
for t in resale:
    year, month = int(t["month"][:4]), int(t["month"][5:7])
    if (year, month) < (2020, 1):
        continue
    if t["flat_type"] != "4 ROOM":
        continue
    key = (t["block"], t["street_name"])
    if key not in block_keys:
        continue
    if key not in level:
        continue
    priced[town_of[key]][level[key]].append(float(t["resale_price"]))

rows = []
for town, by_level in priced.items():
    counts = {c: len(by_level.get(c, [])) for c in (1, 2, 3)}
    if min(counts.values()) < 30:
        continue
    med = {c: round(statistics.median(by_level[c])) for c in (1, 2, 3)}
    rows.append((town, med, counts))
rows.sort(key=lambda r: -r[1][1])

print(f"Q4: {len(rows)} of {len(priced)} towns survive HAVING n>=30")
print(f"{'town':<16}{'l1':>9}{'l2':>9}{'l3':>9}   {'n1':>6}{'n2':>6}{'n3':>6}")
for town, med, counts in rows:
    print(f"{town:<16}{med[1]:>9,.0f}{med[2]:>9,.0f}{med[3]:>9,.0f}   "
          f"{counts[1]:>6,}{counts[2]:>6,}{counts[3]:>6,}")

dropped = sorted(set(priced) - {r[0] for r in rows})
print("\ndropped towns and why:")
for town in dropped:
    by_level = priced[town]
    counts = {c: len(by_level.get(c, [])) for c in (1, 2, 3)}
    missing = [f"level {c}" for c in (1, 2, 3) if counts[c] < 30]
    print(f"  {town:<14} n={counts}  fails: {', '.join(missing)}")

market = {h["hawker_name"]: (h["no_of_mkt_produce_stalls"] not in ("", None)
                             and int(h["no_of_mkt_produce_stalls"]) > 0)
          for h in hawker}
unknown = {h["hawker_name"] for h in hawker
           if h["no_of_mkt_produce_stalls"] in ("", None)}

fresh_near: dict[tuple[str, str], bool] = {k: False for k in block_keys}
unknown_near: set[tuple[str, str]] = set()
for p in prox:
    key = (p["blk_no"], p["street"])
    if int(p["level_id"]) <= 2:
        if market.get(p["hawker_name"], False):
            fresh_near[key] = True
        if p["hawker_name"] in unknown:
            unknown_near.add(key)

def q5(strict: bool) -> dict[bool, tuple[int, float, float]]:
    """strict=True  -> only centres with known produce stalls count
       strict=False -> attribute-unknown centres are treated as having one"""
    flag = dict(fresh_near)
    if not strict:
        for key in unknown_near:
            flag[key] = True
    buckets: dict[bool, list[float]] = defaultdict(list)
    for t in resale:
        year, month = int(t["month"][:4]), int(t["month"][5:7])
        if (year, month) < (2020, 1):
            continue
        if t["flat_type"] != "4 ROOM":
            continue
        key = (t["block"], t["street_name"])
        if key not in flag:
            continue
        buckets[flag[key]].append(float(t["resale_price"]))
    return {k: (len(v), round(statistics.median(v)), round(sum(v) / len(v)))
            for k, v in buckets.items()}

for label, strict in (("Q5 strict ", True), ("Q5 lenient", False)):
    res = q5(strict)
    yes, no = res[True], res[False]
    gap = (yes[1] - no[1]) / no[1] * 100
    print(f"\n{label}: with market  n={yes[0]:>7,}  median={yes[1]:>9,.0f}  mean={yes[2]:>9,.0f}")
    print(f"{'':11}  no market   n={no[0]:>7,}  median={no[1]:>9,.0f}  mean={no[2]:>9,.0f}")
    print(f"{'':11}  median gap  {gap:+.1f}%")

covered = {(p["blk_no"], p["street"]) for p in prox
           if market.get(p["hawker_name"], False)}
unknown_only = {(p["blk_no"], p["street"]) for p in prox
                if p["hawker_name"] in unknown}
no_centre = block_keys - {(p["blk_no"], p["street"]) for p in prox}
print(f"\ncoverage: {len(block_keys) - len(covered):,} of {len(block_keys):,} blocks "
      f"({(len(block_keys) - len(covered)) / len(block_keys) * 100:.1f}%) have no known "
      f"fresh-market centre within 3 km")
print(f"  of those, {len(unknown_only - covered):,} have only attribute-unknown "
      f"centres nearby, {len(no_centre - covered):,} have no centre at all")
