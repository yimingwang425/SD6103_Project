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
town_name = {int(r["town_id"]): r["town_name"] for r in read("town.csv")}
town_of = {b["block_id"]: town_name.get(int(b["town_id"]), "")
           for b in read("hdb_block.csv")}
hawker = read("hawker_centre.csv")
prox = read("hdb_hawker_proximity.csv")
flat_of = {f["flat_id"]: f for f in read("hdb_flat.csv")}
resale = read("resale_transaction.csv")

# hdb_block_proximity_level: MIN(level_id), MIN(distance_m) per block
level: dict[str, int] = {}
nearest: dict[str, float] = {}
for p in prox:
    bid = p["block_id"]
    lv = int(p["level_id"])
    d = float(p["distance_m"])
    if bid not in level or lv < level[bid]:
        level[bid] = lv
    if bid not in nearest or d < nearest[bid]:
        nearest[bid] = d

# ---------------------------------------------------------------- Q4
priced = defaultdict(lambda: defaultdict(list))  # town -> level -> prices
for t in resale:
    if t["transaction_month"] < "2020-01-01":
        continue
    flat = flat_of.get(t["flat_id"])
    if flat is None:                       # INNER JOIN drops NULL / unmatched
        continue
    if flat["flat_type"] != "4 ROOM":
        continue
    bid = flat["block_id"]
    if bid not in level:
        continue
    priced[town_of[bid]][level[bid]].append(float(t["resale_price"]))

rows = []
for town, by_level in priced.items():
    counts = {c: len(by_level.get(c, [])) for c in (1, 2, 3)}
    if min(counts.values()) < 30:          # HAVING n >= 30 for every level
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

# ---------------------------------------------------------------- Q5
# block_fresh: does the block have a fresh-market centre within 1000 m?
market = {h["hawker_id"]: (h["no_of_mkt_produce_stalls"] not in ("", None)
                           and int(h["no_of_mkt_produce_stalls"]) > 0)
          for h in hawker}
unknown = {h["hawker_id"] for h in hawker
           if h["no_of_mkt_produce_stalls"] in ("", None)}

fresh_near: dict[str, bool] = {b: False for b in town_of}
unknown_near: set[str] = set()
for p in prox:
    if float(p["distance_m"]) <= 1000:
        if market.get(p["hawker_id"], False):
            fresh_near[p["block_id"]] = True
        if p["hawker_id"] in unknown:
            unknown_near.add(p["block_id"])


def q5(strict: bool) -> dict[bool, tuple[int, float, float]]:
    """strict=True  -> only centres with known produce stalls count
       strict=False -> attribute-unknown centres are treated as having one"""
    flag = dict(fresh_near)
    if not strict:
        for bid in unknown_near:
            flag[bid] = True
    buckets: dict[bool, list[float]] = defaultdict(list)
    for t in resale:
        if t["transaction_month"] < "2020-01-01":
            continue
        flat = flat_of.get(t["flat_id"])
        if flat is None or flat["flat_type"] != "4 ROOM":
            continue
        if flat["block_id"] not in flag:
            continue
        buckets[flag[flat["block_id"]]].append(float(t["resale_price"]))
    return {k: (len(v), round(statistics.median(v)), round(sum(v) / len(v)))
            for k, v in buckets.items()}


for label, strict in (("Q5 strict ", True), ("Q5 lenient", False)):
    res = q5(strict)
    yes, no = res[True], res[False]
    gap = (yes[1] - no[1]) / no[1] * 100
    print(f"\n{label}: with market  n={yes[0]:>7,}  median={yes[1]:>9,.0f}  mean={yes[2]:>9,.0f}")
    print(f"{'':11}  no market   n={no[0]:>7,}  median={no[1]:>9,.0f}  mean={no[2]:>9,.0f}")
    print(f"{'':11}  median gap  {gap:+.1f}%")

# ---------------------------------------------------------------- coverage
covered = {p["block_id"] for p in prox
           if float(p["distance_m"]) <= 3000 and market.get(p["hawker_id"], False)}
unknown_only = {p["block_id"] for p in prox
                if float(p["distance_m"]) <= 3000
                and p["hawker_id"] in unknown}
no_centre = set(town_of) - {p["block_id"] for p in prox
                            if float(p["distance_m"]) <= 3000}
print(f"\ncoverage: {len(set(town_of)) - len(covered):,} of {len(town_of):,} blocks "
      f"({(len(town_of) - len(covered)) / len(town_of) * 100:.1f}%) have no known "
      f"fresh-market centre within 3 km")
print(f"  of those, {len(unknown_only - covered):,} have only attribute-unknown "
      f"centres nearby, {len(no_centre - covered):,} have no centre at all")
