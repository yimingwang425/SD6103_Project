#!/usr/bin/env python3
"""
05_reroute_onemap.py - re-route every materialised block-hawker pair through
OneMap (SLA, the Singapore national routing service) instead of OSRM, and
write the pair-by-pair comparison table.

Why re-route instead of patching: 04_validate_distances.py --engine onemap
showed the two engines disagree in a specific way - on short pairs OSRM is
sometimes 3-5x the straight-line distance (206 m apart -> OSRM 1,022 m,
OneMap 266 m), which looks like OSRM snapping the origin to the wrong side
of an expressway or canal. Patching only the broken pairs would leave one
dataset built from two cost models; re-routing everything keeps one engine,
one definition.

Cost: OneMap has no matrix endpoint, so this is one request per pair
(63,340). The run is resumable: completed pairs are appended to
data/hawker_geo/onemap_full.jsonl and skipped on re-run; Ctrl-C is safe.

Definition caveat: OneMap reports the network distance between its snapped
endpoints, while the OSRM column is door-to-door (network + snap offsets,
~46 m on average). The merge step adds the OSRM snap offset back onto the
OneMap value; the raw number is preserved in onemap_raw_m for auditing.

    export ONEMAP_TOKEN='...'
    python 05_reroute_onemap.py                 # fetch + merge (~7-8 h)
    python 05_reroute_onemap.py --merge-only    # re-merge from cache, seconds

Outputs (in data/hawker_geo/):
    onemap_distances.csv       block_id, hawker_id, osrm_m, onemap_raw_m, onemap_m, status
    hdb_hawker_walk_onemap.csv same schema as the OSRM file plus distance_source,
                               a drop-in replacement for 03_derive_proximity.py
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

HERE = Path(__file__).resolve().parents[2]          # repo root
OUTDIR = HERE / "data" / "hawker_geo"
PAIRS_CSV = HERE / "data" / "hawker_geo" / "hdb_hawker_proximity.csv"
BLOCKS_CSV = HERE / "data" / "hawker_geo" / "hdb_block.csv"
HAWKER_CSV = HERE / "data" / "hawker_geo" / "hawker_centre.csv"
WALK_CSV = HERE / "data" / "hawker_geo" / "hdb_hawker_walk.csv"
CACHE = HERE / "data" / "hawker_geo" / "onemap_full.jsonl"

ROUTE_URL = "https://www.onemap.gov.sg/api/public/routingsvc/route"
USER_AGENT = "ntu-datasys-project/1.0 (student coursework)"
INTERVAL = 0.20          # initial seconds between requests, globally (5 req/s,
                         # near the published 250 calls/min quota)
MAX_INTERVAL = 1.0       # slowest the pacer may become; 1 req/s floor, so a
                         # 429 burst can never collapse the run
WORKERS = 6


class RateLimiter:
    """Adaptive global pacer: widens the interval on 429, narrows it on success.

    Tuned against the live endpoint (2026-10-22): OneMap answers a 60-request
    burst at 22-35 successful req/s and returns 429 for the rest, so a gentle
    1.15x increase per 429 and a fast recovery (0.97x per 10 successes) settles
    the run at the published 4.2 req/s instead of crawling at 1.1 req/s.
    """

    def __init__(self, interval: float = INTERVAL,
                 min_interval: float = 0.10,
                 max_interval: float = MAX_INTERVAL) -> None:
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
            sleep_for = max(0.0, self.next_at - now)
            self.next_at = max(now, self.next_at) + self.interval
        if sleep_for:
            time.sleep(sleep_for)

    def note_success(self) -> None:
        with self.lock:
            self.successes += 1
            # 10 successes buy back 3% of the interval, so the pacer walks down
            # from the cap to the floor in ~200 requests
            if self.successes % 10 == 0 and self.interval > self.min_interval:
                self.interval = max(self.min_interval, self.interval * 0.97)

    def note_throttle(self) -> None:
        with self.lock:
            self.throttled += 1
            self.interval = min(self.max_interval, self.interval * 1.15)


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
                # honour Retry-After when it is sent, otherwise back off briefly -
                # the global pacer is already widening every worker's slot
                retry_after = exc.headers.get("Retry-After")
                try:
                    pause = float(retry_after) if retry_after else 0.4 * (2 ** attempt)
                except (TypeError, ValueError):
                    pause = 0.4 * (2 ** attempt)
                time.sleep(min(pause, 4.0))
                status = "throttled"
                continue
            status = f"error:{exc.code}"
            time.sleep(1 + attempt)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            # keep the reason: "error:URLError" alone is undiagnosable (it covers
            # DNS failure, connection reset, TLS problems, permission denied and
            # timeouts alike)
            reason = getattr(exc, "reason", exc)
            status = f"error:{type(exc).__name__}:{str(reason)[:80]}"
            time.sleep(1 + attempt)
    return None, status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=WORKERS)
    parser.add_argument("--merge-only", action="store_true",
                        help="skip fetching, rebuild the CSVs from the cache")
    args = parser.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    with BLOCKS_CSV.open(encoding="utf-8-sig") as handle:
        block_coords = {
            r["block_id"]: (float(r["longitude"]), float(r["latitude"]))
            for r in csv.DictReader(handle)
        }
    with HAWKER_CSV.open(encoding="utf-8-sig") as handle:
        hawker_coords = {
            r["hawker_id"]: (float(r["longitude"]), float(r["latitude"]))
            for r in csv.DictReader(handle)
        }

    # the proximity file is the authoritative pair list: exactly the set of
    # pairs inside the 3000 m walking horizon, i.e. what the relation needs
    with PAIRS_CSV.open(encoding="utf-8") as handle:
        pairs = list(csv.DictReader(handle))
    print(f"[pairs] {len(pairs)} materialised block-hawker pairs")

    # OSRM reference values, used for the comparison and for the snap offset
    with WALK_CSV.open(encoding="utf-8") as handle:
        osrm = {(r["block_id"], r["hawker_id"]): r for r in csv.DictReader(handle)}

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    # Only a usable distance counts as "done". A failed request must stay in the
    # todo list, otherwise the cache quietly turns a transient network outage
    # into permanent missing data: the pair is written once with a null
    # distance, is then treated as already fetched on every later run, and is
    # never retried. "no_route" is different: OneMap answered, and the answer
    # is that no walking route exists, so re-asking cannot help.
    TERMINAL = {"ok", "no_route"}
    done: dict[tuple[str, str], dict] = {}
    skipped = 0
    if CACHE.exists():
        with CACHE.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("status") not in TERMINAL:
                    skipped += 1
                    continue
                if record.get("status") == "ok" and record.get("onemap_raw_m") is None:
                    skipped += 1
                    continue
                done[(record["block_id"], record["hawker_id"])] = record
        print(f"[cache] {len(done)} pairs already fetched"
              + (f", {skipped} failed records ignored (will retry)" if skipped else ""))

    if not args.merge_only:
        token = os.environ.get("ONEMAP_TOKEN", "").strip()
        if not token:
            print("error: set ONEMAP_TOKEN to a OneMap API token.")
            return 2

        todo = [p for p in pairs if (p["block_id"], p["hawker_id"]) not in done]
        print(f"[fetch] {len(todo)} to go with {args.workers} workers")

        limiter = RateLimiter()
        lock = threading.Lock()
        started = time.monotonic()
        counts = {"ok": 0, "no_route": 0, "other": 0}
        streak = {"fails": 0}

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
                with CACHE.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record) + "\n")
                done[(block_id, hawker_id)] = record
                counts["ok" if status == "ok" else
                       ("no_route" if status == "no_route" else "other")] += 1
                # circuit breaker: a run that has lost its network looks exactly
                # like a slow run from the outside - every pair still produces a
                # cache line - so without this the process would happily spend
                # hours writing nulls. Sixty consecutive failures across all
                # workers is far outside anything a healthy endpoint produces.
                if status == "ok":
                    streak["fails"] = 0
                else:
                    streak["fails"] += 1
                    if streak["fails"] >= 60:
                        print(f"\n[abort] {streak['fails']} consecutive failures, "
                              f"last status: {status}")
                        print("[abort] the network path is gone, not slow - "
                              "stopping instead of caching more nulls")
                        os._exit(3)
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

    # write the comparison table
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

    dist_path = OUTDIR / "onemap_distances.csv"
    with dist_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    # build a drop-in replacement for 03: same columns as hdb_hawker_walk.csv
    # plus distance_source. Iterate the FULL relation - pairs we did not
    # re-route keep the OSRM value and say so in distance_source.
    usable = 0
    walk_path = OUTDIR / "hdb_hawker_walk_onemap.csv"
    with walk_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "block_id", "blk_no", "street", "postal_code", "town",
            "hawker_id", "walk_distance_m", "walk_duration_s",
            "straight_line_m", "snap_offset_m", "detour_ratio",
            "distance_source",
        ])
        for key, reference in osrm.items():
            record = done.get(key)
            onemap_m = None
            if record is not None and record.get("onemap_raw_m") is not None:
                onemap_m = float(record["onemap_raw_m"]) + float(reference["snap_offset_m"])
            if onemap_m is not None and onemap_m > 0:
                distance = onemap_m
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

    # report
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
    print(f"kept OSRM (not re-routed): {len(osrm) - usable}")
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
