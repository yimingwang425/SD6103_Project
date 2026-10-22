#!/usr/bin/env python3
"""
07_reroute_onemap.py
====================
Re-route **every** materialised block-hawker pair through OneMap (SLA, the
Singapore national routing service) instead of OSRM, so the proximity relation
comes from a single, locally authoritative engine.

Why replace rather than patch
-----------------------------
06_validate_onemap.py showed that OSRM and OneMap disagree in a very specific
way: on long pairs they agree to a few percent, but on short pairs OSRM is
sometimes 3-5x the straight-line distance (e.g. 206 m apart -> OSRM 1,022 m,
OneMap 266 m), which looks like OSRM snapping the origin to the wrong side of
an expressway or canal. Patching only the obviously-broken pairs would leave a
dataset built from two engines with two different cost models - hard to defend
and hard to explain. Routing everything through OneMap keeps one engine, one
definition, one story.

Cost
----
OneMap has no matrix endpoint, so this is one HTTP request per pair: 63,340
requests. Measured throughput on the public endpoint is ~10-20 req/s with a
small amount of 429 backpressure, so the full run is roughly 1.5-2.5 hours. The
run is resumable at any point - every completed pair is appended to
data/onemap_full.jsonl, and a re-run skips whatever is already there.

Definition caveat
-----------------
OneMap reports the **network** distance between its snapped endpoints. Our
OSRM column reports **door-to-door** distance (network + the two snapping
offsets, ~46 m on average). To keep the two comparable the merge step adds the
OSRM snap offset back onto the OneMap value; the raw OneMap number is preserved
in onemap_raw_m so the correction can be audited.

Usage
-----
    export ONEMAP_TOKEN='...'
    python 07_reroute_onemap.py --workers 4          # fetch + merge
    python 07_reroute_onemap.py --merge-only         # re-merge from cache

Outputs
-------
output/onemap_distances.csv   block_id, hawker_id, osrm_m, onemap_raw_m, ...
output/hdb_hawker_walk.csv    NOT overwritten - the OSRM original is kept
output/hdb_hawker_walk_onemap.csv   same schema as the OSRM file, plus
                                    distance_source, for 03_derive_proximity.py
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROUTE_URL = "https://www.onemap.gov.sg/api/public/routingsvc/route"
USER_AGENT = "ntu-datasys-project/1.0 (student coursework)"


class RateLimiter:
    """Adaptive global pacer: widens the interval on 429, narrows it on success."""

    def __init__(self, interval: float = 0.10,
                 min_interval: float = 0.05,
                 max_interval: float = 2.0) -> None:
        self.interval = interval
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.next_at = 0.0
        self.lock = threading.Lock()
        self.successes = 0
        self.throttled = 0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            if now < self.next_at:
                sleep_for = self.next_at - now
            else:
                sleep_for = 0.0
            self.next_at = max(now, self.next_at) + self.interval
        if sleep_for:
            time.sleep(sleep_for)

    def note_success(self) -> None:
        with self.lock:
            self.successes += 1
            # Recover the interval gradually: a single 429 should cost a few
            # seconds, not permanently halve the throughput of a 2-hour run.
            if self.successes % 40 == 0 and self.interval > self.min_interval:
                self.interval = max(self.min_interval, self.interval * 0.92)

    def note_throttle(self) -> None:
        with self.lock:
            self.throttled += 1
            self.interval = min(self.max_interval, self.interval * 1.25)


def fetch_one(token: str,
              start: tuple[float, float],
              end: tuple[float, float],
              limiter: RateLimiter,
              retries: int = 5) -> tuple[float | None, str]:
    """Return (network_distance_m, status) for one walking pair."""
    query = urllib.parse.urlencode({
        "start": f"{start[1]:.7f},{start[0]:.7f}",   # OneMap wants lat,lon
        "end": f"{end[1]:.7f},{end[0]:.7f}",
        "routeType": "walk",
    })
    url = f"{ROUTE_URL}?{query}"
    status = "error"
    for attempt in range(retries):
        limiter.wait()
        try:
            request = urllib.request.Request(
                url, headers={"Authorization": token, "User-Agent": USER_AGENT}
            )
            with urllib.request.urlopen(request, timeout=45) as response:
                data = json.load(response)
            summary = data.get("route_summary") or {}
            distance = summary.get("total_distance")
            limiter.note_success()
            if distance is None:
                return None, "no_route"
            return float(distance), "ok"
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None, "no_route"
            if exc.code == 401:
                return None, "unauthorised"
            if exc.code == 429:
                limiter.note_throttle()
                time.sleep(3 + attempt * 2)
                status = "throttled"
                continue
            status = f"error:{exc.code}"
            time.sleep(1 + attempt)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            status = f"error:{type(exc).__name__}"
            time.sleep(1 + attempt)
    return None, status


def main() -> int:
    here = Path(__file__).resolve().parents[2]  # repo root (scripts/hawker_geo -> scripts -> .)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", default=str(here / "data" / "hawker_geo" / "hdb_hawker_proximity.csv"))
    parser.add_argument("--blocks", default=str(here / "data" / "hawker_geo" / "hdb_block.csv"))
    parser.add_argument("--hawker", default=str(here / "data" / "hawker_geo" / "hawker_centre.csv"))
    parser.add_argument("--outdir", default=str(here / "data" / "hawker_geo"))
    parser.add_argument("--cache", default=str(here / "data" / "hawker_geo" / "onemap_full.jsonl"))
    parser.add_argument("--token-env", default="ONEMAP_TOKEN")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--interval", type=float, default=0.10,
                        help="initial seconds between requests (global)")
    parser.add_argument("--merge-only", action="store_true",
                        help="skip fetching, rebuild the CSVs from the cache")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after N new pairs (0 = all)")
    parser.add_argument("--select", choices=("all", "risk"), default="risk",
                        help="'all' re-routes every pair (63k requests, ~12 h at "
                             "the observed 1.3 req/s); 'risk' re-routes only the "
                             "pairs that can change a proximity class")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    with Path(args.blocks).open(encoding="utf-8-sig") as handle:
        block_coords = {
            r["block_id"]: (float(r["longitude"]), float(r["latitude"]))
            for r in csv.DictReader(handle)
        }
    with Path(args.hawker).open(encoding="utf-8-sig") as handle:
        hawker_coords = {
            r["hawker_id"]: (float(r["longitude"]), float(r["latitude"]))
            for r in csv.DictReader(handle)
        }

    # The proximity file is the authoritative pair list: it is exactly the set
    # of pairs inside the 3000 m walking horizon, i.e. what the relation needs.
    with Path(args.pairs).open(encoding="utf-8") as handle:
        pairs = list(csv.DictReader(handle))
    print(f"[pairs] {len(pairs)} materialised block-hawker pairs")

    # OSRM reference values, used for the comparison and for the snap offset.
    with (here / "data" / "hawker_geo" / "hdb_hawker_walk.csv").open(encoding="utf-8") as handle:
        osrm = {(r["block_id"], r["hawker_id"]): r for r in csv.DictReader(handle)}

    # ---------------- which pairs are actually worth re-routing? ----------------
    # A full re-route is 63,340 requests. The public endpoint sustains roughly
    # 1.3 req/s once its burst allowance is spent, so that is 12+ hours - too
    # long to be sensible. But a distance only changes the *answer* if it can
    # move a class boundary, so we re-route exactly those pairs:
    #
    #   (a) blocks whose nearest centre sits within +-25% of a class boundary
    #       (400-625 m for class 1/2, 800-1250 m for class 2/3). Outside those
    #       windows a 20% distance error cannot change the class.
    #   (b) any pair whose OSRM detour is > 2.5x the straight line - the
    #       failure mode 06_validate_onemap.py identified.
    #
    # For the blocks in (a) we re-route all of their pairs up to 1500 m, because
    # correcting one distance can change *which* centre ends up nearest.
    targets = pairs
    if args.select == "risk":
        nearest_path = here / "data" / "hawker_geo" / "hdb_block_nearest.csv"
        at_risk: set[str] = set()
        if nearest_path.exists():
            with nearest_path.open(encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    distance = float(row["nearest_walk_m"])
                    if 400 <= distance <= 625 or 800 <= distance <= 1250:
                        at_risk.add(row["block_id"])

        selected: set[tuple[str, str]] = set()
        for key, row in osrm.items():
            straight = float(row["straight_line_m"])
            walk = float(row["walk_distance_m"])
            suspicious = straight > 100 and walk / straight > 2.5
            boundary = key[0] in at_risk and walk <= 1500
            if suspicious or boundary:
                selected.add(key)

        targets = [p for p in pairs if (p["block_id"], p["hawker_id"]) in selected]
        print(f"[select] {len(at_risk)} blocks near a class boundary; "
              f"{len(targets)} pairs to re-route "
              f"(of {len(pairs)})")

    cache_path = Path(args.cache)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    done: dict[tuple[str, str], dict] = {}
    if cache_path.exists():
        with cache_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                done[(record["block_id"], record["hawker_id"])] = record
        print(f"[cache] {len(done)} pairs already fetched")

    if not args.merge_only:
        token = os.environ.get(args.token_env, "").strip()
        if not token:
            print(f"error: set {args.token_env} to a OneMap API token.")
            return 2

        todo = [p for p in targets if (p["block_id"], p["hawker_id"]) not in done]
        if args.limit:
            todo = todo[:args.limit]
        print(f"[fetch] {len(todo)} to go with {args.workers} workers")

        limiter = RateLimiter(interval=args.interval)
        lock = threading.Lock()
        started = time.monotonic()
        counts = {"ok": 0, "no_route": 0, "other": 0}

        def work(row: dict) -> None:
            block_id, hawker_id = row["block_id"], row["hawker_id"]
            if block_id not in block_coords or hawker_id not in hawker_coords:
                return
            distance, status = fetch_one(
                token, block_coords[block_id], hawker_coords[hawker_id], limiter
            )
            record = {"block_id": block_id, "hawker_id": hawker_id,
                      "onemap_raw_m": distance, "status": status}
            with lock:
                with cache_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record) + "\n")
                done[(block_id, hawker_id)] = record
                counts["ok" if status == "ok" else
                       ("no_route" if status == "no_route" else "other")] += 1
                total = sum(counts.values())
                if total % 500 == 0 or total == len(todo):
                    elapsed = time.monotonic() - started
                    rate = total / elapsed if elapsed else 0
                    remaining = (len(todo) - total) / rate / 60 if rate else 0
                    print(f"    {total}/{len(todo)}  {rate:.1f} req/s  "
                          f"interval {limiter.interval:.2f}s  "
                          f"429s {limiter.throttled}  ~{remaining:.0f} min left")

        try:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                list(pool.map(work, todo))
        except KeyboardInterrupt:
            print("\ninterrupted - progress is saved, re-run to continue")

        print(f"[fetch] done: ok={counts['ok']} no_route={counts['no_route']} "
              f"other={counts['other']} throttled={limiter.throttled}")

    # ---------------- write the comparison table ---------------- #
    rows = []
    for row in pairs:
        key = (row["block_id"], row["hawker_id"])
        record = done.get(key)
        if not record:
            continue
        reference = osrm.get(key)
        osrm_m = float(reference["walk_distance_m"]) if reference else None
        snap = float(reference["snap_offset_m"]) if reference else 0.0
        raw = record.get("onemap_raw_m")
        rows.append({
            "block_id": key[0],
            "hawker_id": key[1],
            "osrm_m": round(osrm_m, 1) if osrm_m is not None else "",
            "onemap_raw_m": round(raw, 1) if raw is not None else "",
            "onemap_m": round(raw + snap, 1) if raw is not None else "",
            "status": record["status"],
        })

    if not rows:
        print("nothing in the cache yet - nothing to merge")
        return 1

    dist_path = outdir / "onemap_distances.csv"
    with dist_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    # ---------------- build a drop-in replacement for 03 ---------------- #
    # Same columns as hdb_hawker_walk.csv so 03_derive_proximity.py can consume
    # it unchanged, plus distance_source so every row says where it came from.
    usable = 0
    walk_path = outdir / "hdb_hawker_walk_onemap.csv"
    with walk_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "block_id", "blk_no", "street", "postal_code", "town",
            "hawker_id", "walk_distance_m", "walk_duration_s",
            "straight_line_m", "snap_offset_m", "detour_ratio",
            "distance_source",
        ])
        for row in rows:
            key = (row["block_id"], row["hawker_id"])
            reference = osrm.get(key)
            if reference is None:
                continue
            if row["onemap_m"] != "":
                distance = float(row["onemap_m"])
                source = "onemap"
                usable += 1
            else:
                distance = float(reference["walk_distance_m"])
                source = "osrm_fallback"
            straight = float(reference["straight_line_m"])
            writer.writerow([
                key[0], reference["blk_no"], reference["street"],
                reference["postal_code"], reference["town"], key[1],
                round(distance, 1),
                round(distance / 1.39, 1),      # 5 km/h, matches the OSRM column
                round(straight, 1),
                round(float(reference["snap_offset_m"]), 1),
                round(distance / straight, 3) if straight > 0 else "",
                source,
            ])

    # ---------------- report ---------------- #
    ok = [r for r in rows if r["status"] == "ok" and r["osrm_m"] != ""]
    print("\n" + "=" * 66)
    print("ONEMAP RE-ROUTING SUMMARY")
    print("=" * 66)
    print(f"pairs in relation        : {len(pairs)}")
    print(f"fetched                  : {len(rows)}")
    print(f"  ok                     : {sum(1 for r in rows if r['status'] == 'ok')}")
    print(f"  no_route               : {sum(1 for r in rows if r['status'] == 'no_route')}")
    print(f"  other                  : {sum(1 for r in rows if r['status'] not in ('ok', 'no_route'))}")
    print(f"used OneMap distance     : {usable}")
    print(f"fell back to OSRM        : {len(rows) - usable}")
    if ok:
        diffs = [100 * (float(r["onemap_m"]) - float(r["osrm_m"])) / float(r["osrm_m"])
                 for r in ok]
        diffs.sort()
        print(f"\nOneMap vs OSRM (door-to-door): median {statistics.median(diffs):+.1f}%  "
              f"p10 {diffs[len(diffs) // 10]:+.1f}%  p90 {diffs[len(diffs) * 9 // 10]:+.1f}%")
        big = sum(1 for d in diffs if abs(d) > 25)
        print(f"pairs differing > 25%    : {big} ({100 * big / len(diffs):.1f}%)")
    print(f"\nwrote {dist_path}")
    print(f"wrote {walk_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
