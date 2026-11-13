#!/usr/bin/env python3
"""
02_walk_distances.py - OSRM foot routing for every block-centre pair
within 3 km straight-line, into data/hawker_geo/hdb_hawker_walk.csv.
Resumable via osrm_cache.jsonl.
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

HERE = Path(__file__).resolve().parents[4]          # repo root, above supplementary material/Task2
CLEAN_DIR = HERE / "data" / "clean_data"
OUTDIR = HERE / "data" / "hawker_geo"
HDB_CSV = HERE / "data" / "raw_data" / "hdb.csv"
HAWKER_CSV = CLEAN_DIR / "hawker_centre.csv"
WALK_CSV = OUTDIR / "hdb_hawker_walk.csv"
# to build the OneMap variant instead, point WALK_CSV at
# hdb_hawker_walk_onemap.csv (produced by 04_validate_distances.py --full)

CACHE = OUTDIR / "osrm_cache.jsonl"

OSRM_TABLE = "https://routing.openstreetmap.de/routed-foot/table/v1/foot/"
USER_AGENT = "ntu-datasys-project/1.0 (student coursework)"

MAX_COORDS = 100        # server-side limit per /table request
BATCH = 75              # max origins per request
DELAY = 2.0             # seconds between requests; the demo server asks for <= 1 req/s
PRUNE_RADIUS_M = 3000   # straight-line prefilter
# Storage horizon. Because network distance >= great-circle distance, pruning at
# PRUNE_RADIUS_M great-circle metres guarantees every pair within PRUNE_RADIUS_M
# metres of walking has been routed: the neighbourhood is provably complete.
MAX_WALK_M = 3000
EARTH_R = 6371008.8

# the HDB property file uses 3-letter contract-town codes; a block whose code
# is not in this table cannot be placed in a town and is skipped
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

def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in metres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def osrm_table(sources: list[tuple[float, float]],
               destinations: list[tuple[float, float]],
               retries: int = 5) -> dict:
    """Call OSRM /table with an explicit S x D matrix.

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
            # 429 means the demo server's rate limit: back off hard and do not
            # count it as a real failure, so a busy moment does not abort a long run
            is_rate_limit = (isinstance(exc, urllib.error.HTTPError)
                             and exc.code == 429)
            wait = max(10.0, 2 ** attempt) if is_rate_limit else 2 ** attempt
            print(f"    ! request failed ({exc}); retry in {wait:.0f}s "
                  f"[{attempt + 1}/{retries}]")
            time.sleep(wait)
    raise RuntimeError(f"OSRM table failed after {retries} attempts: {last_error}")


def compute_walk_distances(limit: int = 0) -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)

    with HDB_CSV.open(encoding="utf-8-sig") as handle:
        hdb_rows = list(csv.DictReader(handle))
    with HAWKER_CSV.open(encoding="utf-8-sig") as handle:
        hawker_rows = list(csv.DictReader(handle))
    print(f"[load] {len(hdb_rows)} HDB blocks, {len(hawker_rows)} hawker centres")

    blocks = []
    for index, row in enumerate(hdb_rows):
        try:
            lat, lon = float(row["lat"]), float(row["lng"])
        except (TypeError, ValueError):
            continue
        # 76 rows in the source carry the literal string 'NIL' instead of a
        # postal code; it is not a valid code, so it must become NULL rather
        # than reach the VARCHAR column
        postal = row["postal"].strip()
        if not (len(postal) == 6 and postal.isdigit()):
            postal = ""
        town_name = TOWN_CODE_TO_NAME.get(row.get("bldg_contract_town", "").strip())
        if town_name is None:
            print(f"[load][warn] unknown town code "
                  f"{row.get('bldg_contract_town')!r} at row {index + 1}")
            continue
        blocks.append({
            "block_id": f"B{index + 1:05d}",     # legacy id: cache compatibility
            "blk_no": row["blk_no"].strip(),
            "street": row["street"].strip(),
            "postal": postal,
            "town": town_name,
            "lat": lat, "lon": lon,
        })
    hawkers = [{
        "hawker_id": f"H{index + 1:03d}",       # legacy id: cache compatibility
        "lat": float(row["latitude"]),
        "lon": float(row["longitude"]),
    } for index, row in enumerate(hawker_rows)]
    print(f"[load] usable: {len(blocks)} blocks, {len(hawkers)} hawkers")

    if limit:
        blocks = blocks[:limit]
        print(f"[load] --limit active, {len(blocks)} blocks")

    # haversine prefilter: network distance >= great-circle, so any pair beyond
    # PRUNE_RADIUS_M straight-line cannot be within PRUNE_RADIUS_M of walking
    candidates: list[list[int]] = []
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

    # spatial batching: group nearby blocks so one /table call covers many pairs
    CELL = 0.02  # ~2.2 km, keeps a batch spatially tight
    cells: dict[tuple[int, int], list[int]] = defaultdict(list)
    for block_index, block in enumerate(blocks):
        cells[(int(block["lat"] / CELL), int(block["lon"] / CELL))].append(block_index)

    groups: list[list[int]] = []
    for cell_key in sorted(cells):
        indices = sorted(cells[cell_key],
                         key=lambda i: (blocks[i]["lat"], blocks[i]["lon"]))
        for start in range(0, len(indices), BATCH):
            groups.append(indices[start:start + BATCH])

    # split any group whose destination union would blow the 100-coord limit
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

    # resumable cache
    done_groups: set[str] = set()
    if CACHE.exists():
        with CACHE.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    done_groups.add(json.loads(line)["group_key"])
                except (json.JSONDecodeError, KeyError):
                    continue
        print(f"[cache] {len(done_groups)} groups already computed")

    def group_key(group: list[int]) -> str:
        raw = ",".join(blocks[i]["block_id"] for i in group)
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

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
                # add the snap offsets back so the result is door-to-door
                rows.append([
                    blocks[block_index]["block_id"],
                    hawkers[hawker_index]["hawker_id"],
                    round(network + snap_src + snap_dst, 1),
                    round(duration, 1) if duration is not None else "",
                    round(straight[block_index][hawker_index], 1),
                    round(snap_src + snap_dst, 1),
                ])

        with CACHE.open("a", encoding="utf-8") as handle:
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
        time.sleep(DELAY)

    # assemble the output from the cache
    records: list[list] = []
    with CACHE.open(encoding="utf-8") as handle:
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

    # coverage rule: every block must end up with at least one row, otherwise it
    # cannot be assigned a proximity level and Q4 would silently drop it.
    # 103 blocks - almost all in the newer towns (Punggol, Sengkang) where the
    # nearest hawker centre really is more than 3 km away - only qualify
    # through this rule. Their level is 3 either way, so this adds no
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

    output_path = WALK_CSV
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

    # summary
    walk_values = [float(r[2]) for r in kept.values()]
    ratios = [float(r[2]) / float(r[4]) for r in kept.values() if float(r[4]) > 0]
    covered_blocks = {r[0] for r in kept.values()}

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

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0,
                        help="process the first N blocks (smoke test)")
    args = parser.parse_args()
    compute_walk_distances(args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
