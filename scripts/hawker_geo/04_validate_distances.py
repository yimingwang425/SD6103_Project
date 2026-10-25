#!/usr/bin/env python3
"""
04_validate_distances.py
========================
Cross-check the OSRM walking distances (02_compute_walk_distance.py) against an
independent routing engine, on a stratified sample of block-hawker pairs.

    python 04_validate_distances.py --engine valhalla --samples 30
    ONEMAP_TOKEN=... python 04_validate_distances.py --engine onemap --samples 300

--engine valhalla  another OSM-based engine, different implementation and cost
                   model (10x10 matrix requests, ~1 min)
--engine onemap    OneMap, SLA's national network - different data provenance,
                   not OSM at all. Needs a token from
                   https://www.onemap.gov.sg/api/auth/post/getToken (read from
                   the environment only, ~2 min for 300 pairs)

Sampling is stratified by distance so short walks - where the 500/1000 m class
boundaries actually are - are represented. Output: one CSV per engine in
data/hawker_geo/ (router_validation.csv / onemap_validation.csv) plus agreement
statistics. The point of the exercise: two independent engines agreeing to a few
percent means the distance is a property of the road network, not of one
library's quirks.
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
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VALHALLA = "https://valhalla1.openstreetmap.de/sources_to_targets"
ONEMAP_ROUTE = "https://www.onemap.gov.sg/api/public/routingsvc/route"
USER_AGENT = "ntu-datasys-project/1.0 (student coursework)"
CHUNK = 10  # 10 x 10 = 100 cells, the Valhalla server-side maximum


# --------------------------------------------------------------------------- #
# shared: load coordinates and draw a distance-stratified sample
# --------------------------------------------------------------------------- #
def load_context(args):
    here = Path(__file__).resolve().parents[2]  # repo root
    with (here / "data" / "raw_data" / "hdb.csv").open(encoding="utf-8-sig") as fh:
        block_coords = {
            f"B{i + 1:05d}": (float(r["lng"]), float(r["lat"]))
            for i, r in enumerate(csv.DictReader(fh))
        }
    with Path(args.hawker).open(encoding="utf-8-sig") as fh:
        hawker_coords = {
            r["hawker_id"]: (float(r["longitude"]), float(r["latitude"]))
            for r in csv.DictReader(fh)
        }
    with Path(args.walk).open(encoding="utf-8") as fh:
        pairs = list(csv.DictReader(fh))
    if not pairs:
        sys.exit("no routed pairs found - run 02_compute_walk_distance.py first")

    # uniform sampling would be dominated by the 2-3 km pairs where most of the
    # mass sits; stratifying keeps the short walks represented
    pairs.sort(key=lambda r: float(r["walk_distance_m"]))
    random.seed(args.seed)
    per_bucket = max(1, args.samples // args.buckets)
    size = max(1, len(pairs) // args.buckets)
    sample = []
    for b in range(args.buckets):
        window = pairs[b * size:(b + 1) * size]
        if window:
            sample.extend(random.sample(window, min(per_bucket, len(window))))
    print(f"[sample] {len(sample)} pairs across {args.buckets} distance strata")
    return block_coords, hawker_coords, pairs, sample


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


# --------------------------------------------------------------------------- #
# engine: valhalla (matrix endpoint, 10 independent pairs per request)
# --------------------------------------------------------------------------- #
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


def run_valhalla(args, block_coords, hawker_coords, sample):
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


# --------------------------------------------------------------------------- #
# engine: onemap (one pair per request, resumable cache)
# --------------------------------------------------------------------------- #
def token_expiry(token: str) -> float | None:
    """Decode the JWT payload and return the exp claim as a unix timestamp."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, ValueError):
        return None


def onemap_walk(token, start, end, retries: int = 3):
    """Return (distance_m, duration_s, status) for one walking pair.

    status is one of: "ok", "no_route", "unauthorised", "quota", "error:<code>".
    """
    query = urllib.parse.urlencode({
        "start": f"{start[1]:.7f},{start[0]:.7f}",   # OneMap wants lat,lon
        "end": f"{end[1]:.7f},{end[0]:.7f}",
        "routeType": "walk",
    })
    url = f"{ONEMAP_ROUTE}?{query}"
    last_error = "error:unknown"
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url, headers={"Authorization": token, "User-Agent": USER_AGENT}
            )
            with urllib.request.urlopen(request, timeout=45) as response:
                data = json.load(response)
            summary = data.get("route_summary") or {}
            distance = summary.get("total_distance")
            duration = summary.get("total_time")
            if distance is None:
                return None, None, "no_route"
            return float(distance), float(duration) if duration is not None else None, "ok"
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None, None, "no_route"      # outside the pedestrian network
            if exc.code == 401:
                return None, None, "unauthorised"
            if exc.code == 429:
                # undocumented quota: slow down instead of retrying immediately
                time.sleep(15 * (attempt + 1))
                last_error = "quota"
                continue
            last_error = f"error:{exc.code}"
            time.sleep(2 ** attempt)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = f"error:{type(exc).__name__}"
            time.sleep(2 ** attempt)
    return None, None, last_error


def run_onemap(args, block_coords, hawker_coords, sample):
    token = os.environ.get(args.token_env, "").strip()
    if not token:
        sys.exit(f"error: set {args.token_env} to a OneMap API token.\n"
                 f"  register: https://www.onemap.gov.sg/apidocs/register\n"
                 f"  token:    POST https://www.onemap.gov.sg/api/auth/post/getToken")

    expires = token_expiry(token)
    if expires is not None:
        remaining_h = (expires - time.time()) / 3600
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(expires))
        print(f"[token] expires {stamp} ({remaining_h:+.1f} h from now)")
        if remaining_h <= 0:
            sys.exit("error: token has already expired - fetch a new one.")

    # resume cache: a killed run can leave a partial line, so skip bad JSON
    cache_path = Path(args.cache)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    done: dict[tuple[str, str], dict] = {}
    if cache_path.exists():
        with cache_path.open(encoding="utf-8") as fh:
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
    print(f"[fetch] {len(todo)} to go (delay {args.delay}s)")

    fetched = 0
    with cache_path.open("a", encoding="utf-8") as fh:
        for index, row in enumerate(todo, 1):
            block_id, hawker_id = row["block_id"], row["hawker_id"]
            if block_id not in block_coords or hawker_id not in hawker_coords:
                continue
            distance, duration, status = onemap_walk(
                token, block_coords[block_id], hawker_coords[hawker_id]
            )
            if status == "unauthorised":
                sys.exit("error: token rejected (401) - it may have expired.")
            record = {
                "block_id": block_id,
                "hawker_id": hawker_id,
                "osrm_m": float(row["walk_distance_m"]),
                "onemap_m": distance,
                "onemap_s": duration,
                "status": status,
            }
            fh.write(json.dumps(record) + "\n")
            fh.flush()
            done[(block_id, hawker_id)] = record
            fetched += 1
            if index % 25 == 0 or index == len(todo):
                print(f"    {index}/{len(todo)}  ({status})")
            time.sleep(args.delay)

    if fetched == 0 and not done:
        sys.exit("nothing fetched - validation inconclusive")

    results = []
    for row in sample:
        record = done.get((row["block_id"], row["hawker_id"]))
        if not record or record["status"] != "ok" or record["onemap_m"] is None:
            continue
        ours, theirs = record["osrm_m"], record["onemap_m"]
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


# --------------------------------------------------------------------------- #
def main() -> int:
    here = Path(__file__).resolve().parents[2]  # repo root
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("valhalla", "onemap"), required=True)
    parser.add_argument("--walk", default=str(here / "data" / "hawker_geo" / "hdb_hawker_walk.csv"))
    parser.add_argument("--hawker", default=str(here / "data" / "hawker_geo" / "hawker_centre.csv"))
    parser.add_argument("--outdir", default=str(here / "data" / "hawker_geo"))
    parser.add_argument("--cache", default=str(here / "data" / "hawker_geo" / "onemap_cache.jsonl"))
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--buckets", type=int, default=6,
                        help="distance strata to sample from evenly")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--delay", type=float, default=0.2,
                        help="seconds between OneMap requests")
    parser.add_argument("--token-env", default="ONEMAP_TOKEN")
    args = parser.parse_args()

    block_coords, hawker_coords, _pairs, sample = load_context(args)

    if args.engine == "valhalla":
        results = run_valhalla(args, block_coords, hawker_coords, sample)
        out_path = Path(args.outdir) / "router_validation.csv"
    else:
        results = run_onemap(args, block_coords, hawker_coords, sample)
        out_path = Path(args.outdir) / "onemap_validation.csv"

    if not results:
        print("no comparable pairs - validation inconclusive")
        return 1
    report(results, args.engine, out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
