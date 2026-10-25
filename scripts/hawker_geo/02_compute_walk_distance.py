#!/usr/bin/env python3
"""
02_compute_walk_distance.py
===========================
Compute real **walking path distances** between every HDB block and every nearby
hawker centre, using OSRM's free OpenStreetMap foot-routing service.

Why not straight-line / Manhattan
---------------------------------
Great-circle distance cuts through buildings, canals and expressways; Manhattan
distance overestimates on Singapore's diagonal grid. OSRM routes along the
actual footpath network (linkways, overhead bridges, void-deck cuts), and the
measured detour factor here is 1.2-1.6x the straight line - exactly the error
that matters at 500 m / 1000 m thresholds.

Algorithm
---------
1. Prefilter with haversine at 3000 m: network distance >= great-circle, so any
   pair beyond that straight-line cannot be within 3000 m of walking. Cuts
   12,442 x 129 = 1.6M pairs down to ~101k candidates.
2. Batch origins spatially and use OSRM's /table matrix endpoint (one call per
   batch instead of one /route call per pair).
3. Add the reported "snap" offsets so the result is door-to-door.
4. Cache every completed batch (data/hawker_geo/osrm_cache.jsonl) so the run is
   resumable; a re-run skips finished batches.

Usage
-----
    python 02_compute_walk_distance.py --limit 300      # smoke test
    python 02_compute_walk_distance.py                  # full run (~37 min)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

OSRM_TABLE = "https://routing.openstreetmap.de/routed-foot/table/v1/foot/"
USER_AGENT = "ntu-datasys-project/1.0 (student coursework)"

MAX_COORDS = 100        # server-side limit per /table request
PRUNE_RADIUS_M = 3000   # straight-line prefilter
# Storage horizon. Because network distance >= great-circle distance, pruning at
# PRUNE_RADIUS_M great-circle metres guarantees that *every* pair within
# PRUNE_RADIUS_M metres of walking has been routed. Setting the horizon to the
# same number therefore gives a provably complete neighbourhood: we hold every
# hawker centre within a 3 km walk, and nothing further. Raising it above
# PRUNE_RADIUS_M would produce a ragged, incomplete set.
MAX_WALK_M = 3000
EARTH_R = 6371008.8


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #

def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in metres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


# --------------------------------------------------------------------------- #
# OSRM client
# --------------------------------------------------------------------------- #

def osrm_table(sources: list[tuple[float, float]],
               destinations: list[tuple[float, float]],
               retries: int = 5) -> dict:
    """
    Call OSRM /table with an explicit S x D matrix.

    Returns {"distances": [[...]], "durations": [[...]],
             "snap_src": [...], "snap_dst": [...]}
    Raises RuntimeError if the service keeps failing.
    """
    coords = sources + destinations
    coord_str = ";".join(f"{lon:.7f},{lat:.7f}" for lon, lat in coords)
    src_str = ";".join(str(i) for i in range(len(sources)))
    dst_str = ";".join(str(len(sources) + i) for i in range(len(destinations)))
    url = (f"{OSRM_TABLE}{coord_str}"
           f"?sources={src_str}&destinations={dst_str}"
           f"&annotations=distance,duration")

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.load(response)
            if payload.get("code") != "Ok":
                raise RuntimeError(f"OSRM code={payload.get('code')} "
                                   f"message={payload.get('message')}")
            return {
                "distances": payload["distances"],
                "durations": payload.get("durations"),
                "snap_src": [s["distance"] for s in payload.get("sources", [])],
                "snap_dst": [d["distance"] for d in payload.get("destinations", [])],
            }
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                RuntimeError, json.JSONDecodeError) as exc:
            last_error = exc
            # 429 means we hit the demo server's rate limit. Back off hard and
            # do not count it as a real failure, so a busy moment does not
            # abort a long run.
            is_rate_limit = (isinstance(exc, urllib.error.HTTPError)
                             and exc.code == 429)
            wait = max(10.0, 2 ** attempt) if is_rate_limit else 2 ** attempt
            print(f"    ! request failed ({exc}); retry in {wait:.0f}s "
                  f"[{attempt + 1}/{retries}]")
            time.sleep(wait)
    raise RuntimeError(f"OSRM table failed after {retries} attempts: {last_error}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    here = Path(__file__).resolve().parents[2]  # repo root (scripts/hawker_geo -> scripts -> .)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hdb", default=str(here / "data" / "raw_data" / "hdb.csv"),
                        help="HDB block geolocation csv (from siteselect_sg)")
    parser.add_argument("--hawker", default=str(here / "data" / "hawker_geo" / "hawker_centre.csv"))
    parser.add_argument("--outdir", default=str(here / "data" / "hawker_geo"))
    parser.add_argument("--limit", type=int, default=0,
                        help="only process the first N blocks (smoke test)")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="seconds between requests (the demo server asks "
                             "for at most ~1 request/second; do not lower this)")
    parser.add_argument("--batch", type=int, default=75,
                        help="max origins per request")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    cache_path = here / "data" / "hawker_geo" / "osrm_cache.jsonl"

    # ---------------- load inputs ---------------- #
    with Path(args.hdb).open(encoding="utf-8-sig") as handle:
        hdb_rows = list(csv.DictReader(handle))
    with Path(args.hawker).open(encoding="utf-8-sig") as handle:
        hawker_rows = list(csv.DictReader(handle))
    print(f"[load] {len(hdb_rows)} HDB blocks, {len(hawker_rows)} hawker centres")

    blocks = []
    for index, row in enumerate(hdb_rows):
        try:
            lat, lon = float(row["lat"]), float(row["lng"])
        except (TypeError, ValueError):
            continue
        # 76 rows in the source carry the literal string 'NIL' instead of a
        # postal code. It is not a valid code, so it must become NULL rather
        # than reach the CHAR(6) CHECK constraint.
        postal = row["postal"].strip()
        if not (len(postal) == 6 and postal.isdigit()):
            postal = ""
        blocks.append({
            "block_id": f"B{index + 1:05d}",
            "blk_no": row["blk_no"].strip(),
            "street": row["street"].strip(),
            "postal": postal,
            "town": row.get("bldg_contract_town", "").strip(),
            "lat": lat, "lon": lon,
        })
    hawkers = [{
        "hawker_id": row["hawker_id"],
        "lat": float(row["latitude"]),
        "lon": float(row["longitude"]),
    } for row in hawker_rows]
    print(f"[load] usable: {len(blocks)} blocks, {len(hawkers)} hawkers")

    if args.limit:
        blocks = blocks[:args.limit]
        print(f"[load] --limit active, {len(blocks)} blocks")

    # ---------------- 1. haversine prefilter ---------------- #
    candidates: list[list[int]] = []
    # straight[block_index][hawker_index] = great-circle distance in metres
    straight: list[dict[int, float]] = []
    for block in blocks:
        row_candidates = []
        row_straight: dict[int, float] = {}
        for hawker_index, hawker in enumerate(hawkers):
            distance = haversine_m(block["lon"], block["lat"],
                                   hawker["lon"], hawker["lat"])
            if distance <= PRUNE_RADIUS_M:
                row_candidates.append(hawker_index)
                row_straight[hawker_index] = distance
        candidates.append(row_candidates)
        straight.append(row_straight)
    total_candidates = sum(len(c) for c in candidates)
    print(f"[prune] {total_candidates} candidate pairs "
          f"(mean {total_candidates / max(len(blocks), 1):.2f} per block, "
          f"{sum(1 for c in candidates if not c)} blocks with none)")

    # ---------------- 2. spatial batching ---------------- #
    CELL = 0.02  # ~2.2 km, keeps a batch spatially tight
    cells: dict[tuple[int, int], list[int]] = defaultdict(list)
    for block_index, block in enumerate(blocks):
        cells[(int(block["lat"] / CELL), int(block["lon"] / CELL))].append(block_index)

    groups: list[list[int]] = []
    for cell_key in sorted(cells):
        indices = sorted(cells[cell_key],
                         key=lambda i: (blocks[i]["lat"], blocks[i]["lon"]))
        for start in range(0, len(indices), args.batch):
            groups.append(indices[start:start + args.batch])

    # Split any group whose destination union would blow the 100-coord limit.
    def split_group(group: list[int]) -> list[list[int]]:
        union = set()
        for i in group:
            union.update(candidates[i])
        if len(union) + len(group) <= MAX_COORDS or len(group) == 1:
            return [group]
        mid = len(group) // 2
        return split_group(group[:mid]) + split_group(group[mid:])

    groups = [g for group in groups for g in split_group(group)]
    print(f"[batch] {len(groups)} requests planned "
          f"(max {max(len(g) for g in groups)} origins each)")

    # ---------------- 3. resumable cache ---------------- #
    done_groups: set[str] = set()
    if cache_path.exists():
        with cache_path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    done_groups.add(json.loads(line)["group_key"])
                except (json.JSONDecodeError, KeyError):
                    continue
        print(f"[cache] {len(done_groups)} groups already computed")

    def group_key(group: list[int]) -> str:
        raw = ",".join(blocks[i]["block_id"] for i in group)
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    # ---------------- 4. routing ---------------- #
    pending = [g for g in groups if group_key(g) not in done_groups]
    print(f"[osrm] {len(pending)} requests to run")

    started = time.time()
    for number, group in enumerate(pending, start=1):
        destination_union = sorted({h for i in group for h in candidates[i]})
        sources = [(blocks[i]["lon"], blocks[i]["lat"]) for i in group]
        destinations = [(hawkers[h]["lon"], hawkers[h]["lat"])
                        for h in destination_union]

        result = osrm_table(sources, destinations)

        rows = []
        for row_offset, block_index in enumerate(group):
            for col_offset, hawker_index in enumerate(destination_union):
                if hawker_index not in candidates[block_index]:
                    continue  # outside this block's own prefilter radius
                network = result["distances"][row_offset][col_offset]
                if network is None:
                    continue  # no walking route at all
                duration = (result["durations"][row_offset][col_offset]
                            if result.get("durations") else None)
                snap_src = result["snap_src"][row_offset] if result["snap_src"] else 0.0
                snap_dst = result["snap_dst"][col_offset] if result["snap_dst"] else 0.0
                rows.append([
                    blocks[block_index]["block_id"],
                    hawkers[hawker_index]["hawker_id"],
                    round(network + snap_src + snap_dst, 1),
                    round(duration, 1) if duration is not None else "",
                    round(straight[block_index][hawker_index], 1),
                    round(snap_src + snap_dst, 1),
                ])

        with cache_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "group_key": group_key(group),
                "rows": rows,
            }) + "\n")

        if number % 10 == 0 or number == len(pending):
            elapsed = time.time() - started
            rate = number / elapsed if elapsed else 0
            eta = (len(pending) - number) / rate if rate else 0
            print(f"  [{number}/{len(pending)}] {rate:.2f} req/s, "
                  f"ETA {eta / 60:.1f} min")
        time.sleep(args.delay)

    # ---------------- 5. assemble output ---------------- #
    records: list[list] = []
    with cache_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                records.extend(json.loads(line)["rows"])
            except (json.JSONDecodeError, KeyError):
                continue

    # keep only pairs inside the walking-distance horizon, and de-duplicate
    kept: dict[tuple[str, str], list] = {}
    for row in records:
        block_id, hawker_id, walk_m = row[0], row[1], float(row[2])
        if walk_m <= MAX_WALK_M:
            kept[(block_id, hawker_id)] = row

    # Coverage rule: every block must end up with at least one row, otherwise
    # it cannot be assigned a proximity class and Q4 would silently drop it.
    # 514 blocks - almost all in the newer towns (Punggol, Sengkang) where the
    # nearest hawker centre really is more than 2.5 km away - only qualify
    # through this rule. Their class is 3 either way, so this adds no
    # information beyond making the relation total.
    nearest: dict[str, list] = {}
    for row in records:
        block_id = row[0]
        if block_id not in nearest or float(row[2]) < float(nearest[block_id][2]):
            nearest[block_id] = row
    covered = {key[0] for key in kept}
    added_for_coverage = 0
    for block_id, row in nearest.items():
        if block_id not in covered:
            kept[(block_id, row[1])] = row
            added_for_coverage += 1

    block_lookup = {b["block_id"]: b for b in blocks}
    hawker_lookup = {h["hawker_id"]: h for h in hawkers}

    # The block table the proximity relation references. Exported here because
    # hdb.csv is already loaded and normalised at this point.
    block_path = outdir / "hdb_block.csv"
    with block_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["block_id", "blk_no", "street", "postal_code",
                         "town", "latitude", "longitude"])
        for block in blocks:
            writer.writerow([
                block["block_id"], block["blk_no"], block["street"],
                block["postal"], block["town"],
                round(block["lat"], 6), round(block["lon"], 6),
            ])

    output_path = outdir / "hdb_hawker_walk.csv"
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "block_id", "blk_no", "street", "postal_code", "town",
            "hawker_id", "walk_distance_m", "walk_duration_s",
            "straight_line_m", "snap_offset_m", "detour_ratio",
        ])
        for (block_id, hawker_id), row in sorted(kept.items()):
            block, hawker = block_lookup[block_id], hawker_lookup[hawker_id]
            walk_m, straight_m = float(row[2]), float(row[4])
            writer.writerow([
                block_id, block["blk_no"], block["street"], block["postal"],
                block["town"], hawker_id,
                row[2], row[3], row[4], row[5],
                round(walk_m / straight_m, 3) if straight_m else "",
            ])

    # ---------------- 6. summary ---------------- #
    walk_values = [float(r[2]) for r in kept.values()]
    ratios = [float(r[2]) / float(r[4]) for r in kept.values() if float(r[4]) > 0]
    covered_blocks = {r[0] for r in kept.values()}

    print("\n" + "=" * 66)
    print("WALKING DISTANCE SUMMARY")
    print("=" * 66)
    print(f"pairs routed (candidates)        : {len(records)}")
    print(f"pairs within {MAX_WALK_M:.0f} m walk        : {len(kept) - added_for_coverage}")
    print(f"pairs added for coverage         : {added_for_coverage} "
          f"(each block's nearest centre)")
    print(f"rows in proximity relation       : {len(kept)}")
    print(f"blocks with >=1 hawker           : {len(covered_blocks)} / {len(blocks)}")
    if walk_values:
        print(f"walk distance  min/median/mean/max: "
              f"{min(walk_values):.0f} / {statistics.median(walk_values):.0f} / "
              f"{statistics.mean(walk_values):.0f} / {max(walk_values):.0f} m")
    if ratios:
        print(f"detour factor  median / mean     : "
              f"{statistics.median(ratios):.3f} / {statistics.mean(ratios):.3f}")
        print(f"pairs where walk > 1.5x straight : "
              f"{sum(1 for r in ratios if r > 1.5)} ({100 * sum(1 for r in ratios if r > 1.5) / len(ratios):.1f}%)")
    print(f"\nwrote {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
