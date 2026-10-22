#!/usr/bin/env python3
"""Re-implement Q4/Q5 in plain Python so the numbers quoted in the SQL
comments can be checked without a running PostgreSQL server.

percentile_cont(0.5) interpolates linearly, which for an even-sized sample is
exactly the mean of the two middle values -- i.e. statistics.median().
"""
from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
DATA = HERE / "data" / "hawker_geo"


def read(name):
    with (DATA / name).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------- load
blocks = read("hdb_block.csv")
hawker = read("hawker_centre.csv")
prox = read("hdb_hawker_proximity.csv")
resale = read("resale_transaction.csv")

town_of = {b["block_id"]: b["town"] for b in blocks}

# hdb_block_proximity_class: MIN(class), MIN(distance) per block
cls: dict[str, int] = {}
nearest: dict[str, float] = {}
for p in prox:
    bid = p["block_id"]
    c = int(p["proximity_class"])
    d = float(p["walk_distance_m"])
    if bid not in cls or c < cls[bid]:
        cls[bid] = c
    if bid not in nearest or d < nearest[bid]:
        nearest[bid] = d

# ---------------------------------------------------------------- Q4
priced = defaultdict(lambda: defaultdict(list))  # town -> class -> prices
for t in resale:
    if t["flat_type"] != "4 ROOM" or t["month"] < "2020-01":
        continue
    bid = t["block_id"]
    if not bid or bid not in cls:          # INNER JOIN drops NULL / unmatched
        continue
    priced[town_of[bid]][cls[bid]].append(int(t["resale_price"]))

rows = []
for town, by_class in priced.items():
    counts = {c: len(by_class.get(c, [])) for c in (1, 2, 3)}
    if min(counts.values()) < 30:          # HAVING n >= 30 for every class
        continue
    med = {c: round(statistics.median(by_class[c])) for c in (1, 2, 3)}
    rows.append((town, med, counts))
rows.sort(key=lambda r: -r[1][1])

print(f"Q4: {len(rows)} of {len(priced)} towns survive HAVING n>=30")
print(f"{'town':<16}{'c1':>9}{'c2':>9}{'c3':>9}   {'n1':>6}{'n2':>6}{'n3':>6}")
for town, med, counts in rows:
    print(f"{town:<16}{med[1]:>9,}{med[2]:>9,}{med[3]:>9,}   "
          f"{counts[1]:>6,}{counts[2]:>6,}{counts[3]:>6,}")

dropped = sorted(set(priced) - {r[0] for r in rows})
print("\ndropped towns and why:")
for town in dropped:
    by_class = priced[town]
    counts = {c: len(by_class.get(c, [])) for c in (1, 2, 3)}
    missing = [f"class {c}" for c in (1, 2, 3) if counts[c] < 30]
    print(f"  {town:<14} n={counts}  fails: {', '.join(missing)}")

# ---------------------------------------------------------------- Q5
# block_fresh: does the block have a fresh-market centre within 1000 m?
fresh_near: dict[str, bool] = {b["block_id"]: False for b in blocks}
market = {h["hawker_id"]: (h["no_of_mkt_produce_stalls"] not in ("", None)
                           and int(h["no_of_mkt_produce_stalls"]) > 0)
          for h in hawker}
unknown = {h["hawker_id"] for h in hawker
           if h["no_of_mkt_produce_stalls"] in ("", None)}

for p in prox:
    if float(p["walk_distance_m"]) <= 1000 and market.get(p["hawker_id"], False):
        fresh_near[p["block_id"]] = True


def q5(strict: bool) -> dict[bool, tuple[int, float, float]]:
    """strict=True  -> only centres with known produce stalls count
       strict=False -> attribute-unknown centres are treated as having one"""
    flag = dict(fresh_near)
    if not strict:
        for p in prox:
            if float(p["walk_distance_m"]) <= 1000 and p["hawker_id"] in unknown:
                flag[p["block_id"]] = True
    buckets: dict[bool, list[int]] = defaultdict(list)
    for t in resale:
        if t["flat_type"] != "4 ROOM" or t["month"] < "2020-01":
            continue
        bid = t["block_id"]
        if not bid or bid not in flag:
            continue
        buckets[flag[bid]].append(int(t["resale_price"]))
    return {k: (len(v), round(statistics.median(v)), round(sum(v) / len(v)))
            for k, v in buckets.items()}


for label, strict in (("Q5 strict ", True), ("Q5 lenient", False)):
    res = q5(strict)
    yes, no = res[True], res[False]
    gap = (yes[1] - no[1]) / no[1] * 100
    print(f"\n{label}: with market  n={yes[0]:>7,}  median={yes[1]:>9,}  mean={yes[2]:>9,}")
    print(f"{'':11}  no market   n={no[0]:>7,}  median={no[1]:>9,}  mean={no[2]:>9,}")
    print(f"{'':11}  median gap  {gap:+.1f}%")

# ---------------------------------------------------------------- coverage
covered = {p["block_id"] for p in prox
           if float(p["walk_distance_m"]) <= 3000 and market.get(p["hawker_id"], False)}
unknown_only = {p["block_id"] for p in prox
                if float(p["walk_distance_m"]) <= 3000
                and p["hawker_id"] in unknown}
no_centre = set(town_of) - {p["block_id"] for p in prox
                            if float(p["walk_distance_m"]) <= 3000}
print(f"\ncoverage: {len(set(town_of)) - len(covered):,} of {len(town_of):,} blocks "
      f"({(len(town_of) - len(covered)) / len(town_of) * 100:.1f}%) have no known "
      f"fresh-market centre within 3 km")
print(f"  of those, {len(unknown_only - covered):,} have only attribute-unknown "
      f"centres nearby, {len(no_centre - covered):,} have no centre at all")
