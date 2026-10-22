#!/usr/bin/env python3
"""
05_validate_osrm.py
===================
Sanity-check the OSRM walking distances against an *independent* routing engine
(Valhalla, also free, also built from OpenStreetMap but a completely different
implementation and pedestrian cost model).

Method
------
Pick N block-hawker pairs spanning the whole distance range, ask Valhalla for
the same walking distance, and compare. Valhalla's public matrix endpoint caps
a request at 100 cells, so we send 10 sources x 10 targets and read the
diagonal - that gives 10 independent pairs per request.

This is the answer to "how do you know your distances are right?" in the viva:
two independent engines agreeing to within a few percent means the number is a
property of the road network, not of one library's quirks.

Usage
-----
    python 05_validate_osrm.py --samples 30
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

VALHALLA = "https://valhalla1.openstreetmap.de/sources_to_targets"
USER_AGENT = "ntu-datasys-project/1.0 (student coursework)"
CHUNK = 10  # 10 x 10 = 100 cells, the server-side maximum


def valhalla_matrix(sources: list[tuple[float, float]],
                    targets: list[tuple[float, float]],
                    retries: int = 4) -> list[list[float | None]]:
    """Return a len(sources) x len(targets) matrix of walking distances in metres."""
    body = {
        "sources": [{"lat": lat, "lon": lon} for lon, lat in sources],
        "targets": [{"lat": lat, "lon": lon} for lon, lat in targets],
        "costing": "pedestrian",
        "units": "kilometers",
    }
    payload = json.dumps(body).encode()
    last: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                VALHALLA, data=payload,
                headers={"Content-Type": "application/json",
                         "User-Agent": USER_AGENT},
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.load(response)
            matrix = []
            for row in data["sources_to_targets"]:
                matrix.append([
                    None if cell.get("distance") is None else cell["distance"] * 1000
                    for cell in row
                ])
            return matrix
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                KeyError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Valhalla failed: {last}")


def main() -> int:
    here = Path(__file__).resolve().parents[2]  # repo root (scripts/hawker_geo -> scripts -> .)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--walk", default=str(here / "data" / "hawker_geo" / "hdb_hawker_walk.csv"))
    parser.add_argument("--hdb", default=str(here / "data" / "raw_data" / "hdb.csv"))
    parser.add_argument("--hawker", default=str(here / "data" / "hawker_geo" / "hawker_centre.csv"))
    parser.add_argument("--outdir", default=str(here / "data" / "hawker_geo"))
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    with Path(args.hdb).open(encoding="utf-8-sig") as handle:
        hdb_rows = list(csv.DictReader(handle))
    block_coords = {
        f"B{i + 1:05d}": (float(r["lng"]), float(r["lat"]))
        for i, r in enumerate(hdb_rows)
    }
    with Path(args.hawker).open(encoding="utf-8-sig") as handle:
        hawker_rows = list(csv.DictReader(handle))
    hawker_coords = {
        r["hawker_id"]: (float(r["longitude"]), float(r["latitude"]))
        for r in hawker_rows
    }

    with Path(args.walk).open(encoding="utf-8") as handle:
        pairs = list(csv.DictReader(handle))
    if not pairs:
        print("no routed pairs found - run 02_compute_walk_distance.py first")
        return 1

    # Sample across the distance range so the validation is not dominated by
    # very short walks (where every engine agrees trivially).
    pairs.sort(key=lambda r: float(r["walk_distance_m"]))
    random.seed(args.seed)
    buckets = 6
    per_bucket = max(1, args.samples // buckets)
    sample = []
    size = len(pairs) // buckets
    for b in range(buckets):
        window = pairs[b * size:(b + 1) * size] or pairs
        sample.extend(random.sample(window, min(per_bucket, len(window))))
    print(f"[sample] {len(sample)} pairs across {buckets} distance buckets")

    results = []
    for start in range(0, len(sample), CHUNK):
        chunk = sample[start:start + CHUNK]
        # Valhalla's matrix needs distinct endpoints; skip any degenerate chunk.
        sources = [block_coords[r["block_id"]] for r in chunk]
        targets = [hawker_coords[r["hawker_id"]] for r in chunk]
        if len(set(chunk[i]["block_id"] for i in range(len(chunk)))) != len(chunk):
            print("  (skipping chunk with duplicate blocks)")
            continue
        try:
            matrix = valhalla_matrix(sources, targets)
        except RuntimeError as exc:
            print(f"  ! {exc}")
            continue
        for offset, row in enumerate(chunk):
            reference = matrix[offset][offset]
            if reference is None:
                continue
            ours = float(row["walk_distance_m"])
            results.append({
                "block_id": row["block_id"],
                "hawker_id": row["hawker_id"],
                "osrm_m": ours,
                "valhalla_m": round(reference, 1),
                "abs_diff_m": round(ours - reference, 1),
                "rel_diff_pct": round(100 * (ours - reference) / reference, 2),
            })
        time.sleep(1.5)

    if not results:
        print("no comparable pairs - validation inconclusive")
        return 1

    out_path = Path(args.outdir) / "router_validation.csv"
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    rel = [abs(r["rel_diff_pct"]) for r in results]
    signed = [r["rel_diff_pct"] for r in results]
    print("\n" + "=" * 66)
    print("OSRM vs VALHALLA WALKING DISTANCE AGREEMENT")
    print("=" * 66)
    print(f"comparable pairs          : {len(results)}")
    print(f"absolute relative error   : median {statistics.median(rel):.2f}%  "
          f"mean {statistics.mean(rel):.2f}%  max {max(rel):.2f}%")
    print(f"signed relative error     : median {statistics.median(signed):+.2f}%  "
          f"mean {statistics.mean(signed):+.2f}%")
    print(f"pairs agreeing within 5%  : "
          f"{sum(1 for r in rel if r <= 5)} / {len(rel)} "
          f"({100 * sum(1 for r in rel if r <= 5) / len(rel):.0f}%)")
    print(f"pairs agreeing within 10% : "
          f"{sum(1 for r in rel if r <= 10)} / {len(rel)} "
          f"({100 * sum(1 for r in rel if r <= 10) / len(rel):.0f}%)")
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
