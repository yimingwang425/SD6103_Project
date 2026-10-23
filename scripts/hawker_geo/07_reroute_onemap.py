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
requests. The run is resumable at any point - every completed pair is appended
to data/hawker_geo/onemap_full.jsonl, and a re-run skips whatever is there.

Throughput was measured against the live endpoint on 2026-10-22 and again over
a full day of running on 2026-10-23, and both of the earlier guesses in this
docstring ("10-20 req/s" and then "2.3 req/s sustained") were too optimistic:

    burst, 60 requests   22-35 successful req/s  (the rest are 429)
    one 700-pair slice    1.1-4.0 successful req/s, median ~1.4
    sustained             ~1.4 successful req/s

The burst allowance replenishes unevenly, which is why a single 90-second
sample reads 2.3 and a whole run settles near 1.4. The server throttles on
*request rate*, not on concurrency, so raising the worker count does not help -
14 workers measured 2.2 req/s with three times the 429s of 6 workers. Budget
about 8 hours for the full 63,340-pair relation, not the 1.5-2.5 this file
originally claimed.

That is why --max-walk-m exists. Re-routing everything buys very little: the
class boundaries are at 500 m and 1000 m, so once every pair within 1500 m is
done, no remaining pair can be class 1 or 2, and pulling one of them under
1000 m would need a >33% shortening - well into the tail of the observed
OneMap/OSRM ratio. `--max-walk-m 1500` finishes the part that can move an
answer in about 3.5 hours; the rest is 8.

(What *was* fixable was this script's own pacing: the original limiter widened
1.25x per 429 but recovered only 0.92x per 40 successes, and the 429 path slept
3+2*attempt seconds per worker. Together those held the run at 1.1 req/s
against a server willing to serve more - see RateLimiter for the measured
before/after.)

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
    """Adaptive global pacer: widens the interval on 429, narrows it on success.

    The tuning here is not arbitrary - it was measured against the live endpoint
    (2026-10-22). OneMap answers a 60-request burst at 22-35 successful req/s
    and simply returns 429 for the rest, so the service is nowhere near the
    bottleneck. What *was* the bottleneck is this class as originally written:
    it widened by 1.25x on every 429 but only narrowed by 0.92x per 40
    successes, so any early burst of 429s pushed the interval to the 2.0 s cap
    and it then needed ~680 successes to walk back down - during which the whole
    run crawled at ~1.1 req/s instead of the 4-5 req/s the server allows.

    So: a gentler multiplicative increase (1.15x), a much faster recovery
    (0.97x every 10 successes) and a lower cap (1.0 s, i.e. never slower than
    1 req/s). The published quota is 250 calls/min = 4.2 req/s, which is what
    the run should settle at.
    """

    def __init__(self, interval: float = 0.20,
                 min_interval: float = 0.10,
                 max_interval: float = 1.0) -> None:
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
            # Recover quickly: 10 successes buy back 3% of the interval, so the
            # pacer walks down from the cap to the floor in ~200 requests
            # instead of ~680. A brief 429 burst should cost seconds, not the
            # throughput of the whole run.
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
                # The old pause was 3 + 2*attempt seconds (3,5,7,9,11). That was
                # the single biggest throughput killer: with only a handful of
                # workers, a few 429s parked every thread for seconds at a time
                # and the run measured 1.1 req/s against a server that happily
                # serves 5+. Honour Retry-After when it is sent, otherwise back
                # off briefly - the global pacer is already widening every
                # worker's slot, so this does not need to be generous as well.
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
            # Keep the reason. "error:URLError" alone is undiagnosable: it covers
            # DNS failure, connection reset, TLS problems, permission denied and
            # timeouts alike, and on 2026-10-23 the cache filled with 14,450 of
            # them without ever saying which one it was.
            reason = getattr(exc, "reason", exc)
            status = f"error:{type(exc).__name__}:{str(reason)[:80]}"
            time.sleep(1 + attempt)
    return None, status


def main() -> int:
    here = Path(__file__).resolve().parents[2]  # repo root
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", default=str(here / "data" / "hawker_geo" / "hdb_hawker_proximity.csv"))
    parser.add_argument("--blocks", default=str(here / "data" / "hawker_geo" / "hdb_block.csv"))
    parser.add_argument("--hawker", default=str(here / "data" / "hawker_geo" / "hawker_centre.csv"))
    parser.add_argument("--outdir", default=str(here / "data" / "hawker_geo"))
    parser.add_argument("--cache", default=str(here / "data" / "hawker_geo" / "onemap_full.jsonl"))
    parser.add_argument("--token-env", default="ONEMAP_TOKEN")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--interval", type=float, default=0.20,
                        help="initial seconds between requests, globally "
                             "(0.20 = 5 req/s, near the published 250 calls/min)")
    parser.add_argument("--max-interval", type=float, default=1.0,
                        help="slowest the pacer may become; 1.0 = a 1 req/s "
                             "floor, so a 429 burst can never collapse the run")
    parser.add_argument("--merge-only", action="store_true",
                        help="skip fetching, rebuild the CSVs from the cache")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after N new pairs (0 = all)")
    parser.add_argument("--max-seconds", type=float, default=0.0,
                        help="stop after this many seconds (0 = no limit). "
                             "Useful for driving the run in bounded slices: "
                             "throughput swings between 1.1 and 4.0 req/s "
                             "depending on how much burst allowance the "
                             "endpoint has left, so a fixed pair count either "
                             "overruns the window or wastes most of it")
    parser.add_argument("--select", choices=("all", "risk"), default="risk",
                        help="'all' re-routes every pair (63k requests, ~7-8 h "
                             "at the measured 2.3 req/s sustained); 'risk' "
                             "re-routes only the pairs that can change a "
                             "proximity class")
    parser.add_argument("--max-walk-m", type=float, default=0.0,
                        help="only re-route pairs whose OSRM walk is at most "
                             "this many metres (0 = no limit). Use 1000 to "
                             "finish every pair that can be class 1 or 2: the "
                             "remaining class-3 pairs are far by construction "
                             "and cannot move any query answer")
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

    # Note this narrows what gets *fetched*, never what gets written out: the
    # merge below still walks the full `pairs` list, so a partial run leaves the
    # output CSVs incomplete rather than truncated to the filtered subset.
    if args.max_walk_m:
        before = len(targets)
        targets = [p for p in targets
                   if float(p["walk_distance_m"]) <= args.max_walk_m]
        print(f"[filter] {len(targets)} of {before} pairs within "
              f"{args.max_walk_m:.0f} m walk")

    cache_path = Path(args.cache)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    # Only a usable distance counts as "done". A failed request must stay in the
    # todo list, otherwise the cache quietly turns a transient network outage
    # into permanent missing data: the pair is written once with a null
    # distance, is then treated as already fetched on every later run, and is
    # never retried. That is exactly what happened on 2026-10-23 - 14,450
    # consecutive URLErrors were cached as if they were results.
    #
    # "no_route" is different: OneMap answered, and the answer is that no
    # walking route exists, so re-asking cannot help.
    TERMINAL = {"ok", "no_route"}
    done: dict[tuple[str, str], dict] = {}
    skipped = 0
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
        token = os.environ.get(args.token_env, "").strip()
        if not token:
            print(f"error: set {args.token_env} to a OneMap API token.")
            return 2

        todo = [p for p in targets if (p["block_id"], p["hawker_id"]) not in done]
        if args.limit:
            todo = todo[:args.limit]
        print(f"[fetch] {len(todo)} to go with {args.workers} workers")

        limiter = RateLimiter(interval=args.interval,
                              max_interval=args.max_interval)
        lock = threading.Lock()
        started = time.monotonic()
        deadline = started + args.max_seconds if args.max_seconds else None
        counts = {"ok": 0, "no_route": 0, "other": 0}
        streak = {"fails": 0}

        def work(row: dict) -> None:
            # Past the time budget, return without fetching. pool.map still walks
            # the remaining rows, but each is a no-op, so the slice ends close to
            # its deadline instead of mid-request. Nothing is lost: those pairs
            # simply stay in the todo list for the next slice.
            if deadline is not None and time.monotonic() > deadline:
                return
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
                # Circuit breaker. A run that has lost its network looks exactly
                # like a slow run from the outside - every pair still produces a
                # cache line - so without this the process will happily spend
                # twelve hours writing nulls, which is what it did on
                # 2026-10-23. Sixty consecutive failures across all workers is
                # far outside anything a healthy endpoint produces.
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
        # Iterate the FULL relation, not just the re-routed subset: the point
        # of this file is to be a drop-in replacement for hdb_hawker_walk.csv,
        # so every pair must be present. Pairs we did not re-route keep the
        # OSRM value and say so in distance_source.
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
