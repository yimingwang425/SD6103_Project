#!/usr/bin/env python3
"""
04_validate_distances.py - cross-check the OSRM walking distances from
02_walk_distances.py against independent engines (--engine valhalla |
onemap --samples N), or re-route every pair through OneMap
(--engine onemap --full, needs ONEMAP_TOKEN).
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import random
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
HDB_CSV = HERE / "data" / "raw_data" / "hdb.csv"
HAWKER_CSV = HERE / "data" / "clean_data" / "hawker_centre.csv"
WALK_CSV = HERE / "data" / "hawker_geo" / "hdb_hawker_walk.csv"
OUTDIR = HERE / "data" / "hawker_geo"
CACHE_SAMPLE = HERE / "data" / "hawker_geo" / "onemap_cache.jsonl"
CACHE_FULL = HERE / "data" / "hawker_geo" / "onemap_full.jsonl"

VALHALLA = "https://valhalla1.openstreetmap.de/sources_to_targets"
ONEMAP_ROUTE = "https://www.onemap.gov.sg/api/public/routingsvc/route"
USER_AGENT = "ntu-datasys-project/1.0 (student coursework)"
CHUNK = 10          # 10 x 10 = 100 cells, the Valhalla server-side maximum
BUCKETS = 6         # distance strata to sample from evenly
SEED = 42
DELAY = 0.2         # seconds between OneMap requests (sample mode)
INTERVAL = 0.20     # initial global pacing interval (full mode)
MAX_INTERVAL = 1.0
WORKERS = 6


def load_coords() -> tuple[dict, dict]:
    """Block and hawker coordinates keyed by the legacy Bxxxxx/Hxxx ids."""
    with HDB_CSV.open(encoding="utf-8-sig") as handle:
        block_coords = {
            f"B{index + 1:05d}": (float(r["lng"]), float(r["lat"]))
            for index, r in enumerate(csv.DictReader(handle))
        }
    with HAWKER_CSV.open(encoding="utf-8-sig") as handle:
        hawker_coords = {
            f"H{index + 1:03d}": (float(r["longitude"]), float(r["latitude"]))
            for index, r in enumerate(csv.DictReader(handle))
        }
    return block_coords, hawker_coords


def load_pairs() -> list[dict]:
    with WALK_CSV.open(encoding="utf-8") as handle:
        pairs = list(csv.DictReader(handle))
    if not pairs:
        sys.exit("no routed pairs found - run 02_walk_distances.py first")
    return pairs


def stratified_sample(pairs: list[dict], samples: int) -> list[dict]:
    """Uniform sampling would be dominated by the 2-3 km pairs where most of
    the mass sits; stratifying keeps the short walks represented."""
    pairs.sort(key=lambda r: float(r["walk_distance_m"]))
    random.seed(SEED)
    per_bucket = max(1, samples // BUCKETS)
    size = max(1, len(pairs) // BUCKETS)
    sample = []
    for b in range(BUCKETS):
        window = pairs[b * size:(b + 1) * size]
        if window:
            sample.extend(random.sample(window, min(per_bucket, len(window))))
    print(f"[sample] {len(sample)} pairs across {BUCKETS} distance strata")
    return sample


def report(results, engine, out_path):
    """Write the per-pair CSV and print agreement statistics."""
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    rel = [abs(r["rel_diff_pct"]) for r in results]
    signed = [r["rel_diff_pct"] for r in results]
    print("\n" + "=" * 66)
    print(f"OSRM vs {engine.upper()} WALKING DISTANCE AGREEMENT")
    print("=" * 66)
    print(f"comparable pairs          : {len(results)}")
    print(f"absolute relative error   : median {statistics.median(rel):.2f}%  "
          f"mean {statistics.mean(rel):.2f}%  max {max(rel):.2f}%")
    print(f"signed relative error     : median {statistics.median(signed):+.2f}%  "
          f"mean {statistics.mean(signed):+.2f}%")
    for threshold in (5, 10, 15):
        within = sum(1 for r in rel if r <= threshold)
        print(f"pairs agreeing within {threshold:2d}% : {within} / {len(rel)} "
              f"({100 * within / len(rel):.0f}%)")
    print(f"\nwrote {out_path}")


# ---------------------------------------------------------------- Valhalla
def valhalla_matrix(sources, targets, retries: int = 4):
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
            return [
                [None if cell.get("distance") is None else cell["distance"] * 1000
                 for cell in row]
                for row in data["sources_to_targets"]
            ]
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                KeyError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Valhalla failed: {last}")


def run_valhalla(sample, block_coords, hawker_coords):
    results = []
    for start in range(0, len(sample), CHUNK):
        chunk = sample[start:start + CHUNK]
        # the matrix diagonal is only one pair per endpoint when endpoints repeat
        if len({r["block_id"] for r in chunk}) != len(chunk):
            print("  (skipping chunk with duplicate blocks)")
            continue
        try:
            matrix = valhalla_matrix([block_coords[r["block_id"]] for r in chunk],
                                     [hawker_coords[r["hawker_id"]] for r in chunk])
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
    return results


# ------------------------------------------------------------------ OneMap
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
            if self.successes % 10 == 0 and self.interval > 0.10:
                self.interval = max(0.10, self.interval * 0.97)

    def note_throttle(self) -> None:
        with self.lock:
            self.throttled += 1
            self.interval = min(self.max_interval, self.interval * 1.15)


def fetch_one(token: str,
              start: tuple[float, float],
              end: tuple[float, float],
              limiter: RateLimiter,
              retries: int = 5) -> tuple[float | None, str]:
    """Return (network_distance_m, status) for one walking pair.

    status is one of: "ok", "no_route", "unauthorised", "throttled",
    "error", "error:<code>", "error:<Type>:<reason>".
    """
    query = urllib.parse.urlencode({
        "start": f"{start[1]:.7f},{start[0]:.7f}",   # OneMap wants lat,lon
        "end": f"{end[1]:.7f},{end[0]:.7f}",
        "routeType": "walk",
    })
    url = f"{ONEMAP_ROUTE}?{query}"
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


def token_expiry(token: str) -> float | None:
    """Decode the JWT payload and return the exp claim as a unix timestamp."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, ValueError):
        return None


def require_token() -> str:
    token = os.environ.get("ONEMAP_TOKEN", "").strip()
    if not token:
        sys.exit("error: set ONEMAP_TOKEN to a OneMap API token.\n"
                 "  register: https://www.onemap.gov.sg/apidocs/register\n"
                 "  token:    POST https://www.onemap.gov.sg/api/auth/post/getToken")
    expires = token_expiry(token)
    if expires is not None:
        remaining_h = (expires - time.time()) / 3600
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(expires))
        print(f"[token] expires {stamp} ({remaining_h:+.1f} h from now)")
        if remaining_h <= 0:
            sys.exit("error: token has already expired - fetch a new one.")
    return token


def run_onemap_sample(sample, block_coords, hawker_coords):
    """Sequential fetch over the stratified sample; compares against OSRM."""
    token = require_token()
    limiter = RateLimiter()

    # resume cache: a killed run can leave a partial line, so skip bad JSON
    CACHE_SAMPLE.parent.mkdir(parents=True, exist_ok=True)
    done: dict[tuple[str, str], dict] = {}
    if CACHE_SAMPLE.exists():
        with CACHE_SAMPLE.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                done[(record["block_id"], record["hawker_id"])] = record
        print(f"[cache] {len(done)} pairs already fetched")

    todo = [r for r in sample if (r["block_id"], r["hawker_id"]) not in done]
    print(f"[fetch] {len(todo)} to go (delay {DELAY}s)")

    fetched = 0
    with CACHE_SAMPLE.open("a", encoding="utf-8") as fh:
        for index, row in enumerate(todo, 1):
            block_id, hawker_id = row["block_id"], row["hawker_id"]
            if block_id not in block_coords or hawker_id not in hawker_coords:
                continue
            distance, status = fetch_one(
                token, block_coords[block_id], hawker_coords[hawker_id], limiter
            )
            if status == "unauthorised":
                sys.exit("error: token rejected (401) - it may have expired.")
            record = {
                "block_id": block_id,
                "hawker_id": hawker_id,
                "osrm_m": float(row["walk_distance_m"]),
                "onemap_raw_m": distance,
                "status": status,
            }
            fh.write(json.dumps(record) + "\n")
            fh.flush()
            done[(block_id, hawker_id)] = record
            fetched += 1
            if index % 25 == 0 or index == len(todo):
                print(f"    {index}/{len(todo)}  ({status})")
            time.sleep(DELAY)

    if fetched == 0 and not done:
        sys.exit("nothing fetched - validation inconclusive")

    results = []
    for row in sample:
        record = done.get((row["block_id"], row["hawker_id"]))
        # .get(): cache lines written before the 04/05 merge lack onemap_raw_m
        if not record or record["status"] != "ok" or record.get("onemap_raw_m") is None:
            continue
        ours, theirs = record["osrm_m"], record["onemap_raw_m"]
        if theirs <= 0:
            continue
        results.append({
            "block_id": row["block_id"],
            "hawker_id": row["hawker_id"],
            "osrm_m": round(ours, 1),
            "onemap_m": round(theirs, 1),
            "abs_diff_m": round(ours - theirs, 1),
            "rel_diff_pct": round(100 * (ours - theirs) / theirs, 2),
        })
    return results


def run_onemap_full(pairs, args):
    """Re-route every materialised pair through OneMap, then merge."""
    token = "" if args.merge_only else require_token()

    block_coords, hawker_coords = load_coords()
    print(f"[pairs] {len(pairs)} materialised block-hawker pairs")

    # OSRM reference values, used for the comparison and for the snap offset
    osrm = {(r["block_id"], r["hawker_id"]): r for r in pairs}

    CACHE_FULL.parent.mkdir(parents=True, exist_ok=True)
    # Only a usable distance counts as "done". A failed request must stay in the
    # todo list, otherwise the cache quietly turns a transient network outage
    # into permanent missing data: the pair is written once with a null
    # distance, is then treated as already fetched on every later run, and is
    # never retried. "no_route" is different: OneMap answered, and the answer
    # is that no walking route exists, so re-asking cannot help.
    TERMINAL = {"ok", "no_route"}
    done: dict[tuple[str, str], dict] = {}
    skipped = 0
    if CACHE_FULL.exists():
        with CACHE_FULL.open(encoding="utf-8") as handle:
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
                with CACHE_FULL.open("a", encoding="utf-8") as handle:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("valhalla", "onemap"), required=True,
                        help="routing engine to cross-check / re-route with")
    parser.add_argument("--samples", type=int, default=30,
                        help="sample mode: how many pairs (stratified by distance)")
    parser.add_argument("--full", action="store_true",
                        help="onemap only: re-route every pair, not a sample")
    parser.add_argument("--workers", type=int, default=WORKERS,
                        help="onemap --full: parallel request threads")
    parser.add_argument("--merge-only", action="store_true",
                        help="onemap --full only: skip fetching, rebuild the CSVs from the cache")
    args = parser.parse_args()

    block_coords, hawker_coords = load_coords()
    pairs = load_pairs()

    if args.engine == "valhalla":
        results = run_valhalla(stratified_sample(pairs, args.samples),
                               block_coords, hawker_coords)
        out_path = OUTDIR / "router_validation.csv"
    elif args.full:
        return run_onemap_full(pairs, args)
    else:
        results = run_onemap_sample(stratified_sample(pairs, args.samples),
                                    block_coords, hawker_coords)
        out_path = OUTDIR / "onemap_validation.csv"

    if not results:
        print("no comparable pairs - validation inconclusive")
        return 1
    report(results, args.engine, out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
