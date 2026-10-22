#!/usr/bin/env python3
"""
06_validate_onemap.py
=====================
Cross-check the OSRM walking distances against **OneMap**, the authoritative
Singapore national map service run by the Singapore Land Authority (SLA).

Why a third engine, and why this one
------------------------------------
05_validate_osrm.py already compares OSRM against Valhalla, but both are built
from OpenStreetMap and share the same blind spots: OSM's Singapore pedestrian
coverage is volunteer-maintained, and the thing it most often misses is exactly
what matters for a 500 m / 1000 m threshold - HDB void decks, covered linkways
and estate-level footpaths. OneMap routes on SLA's own network, so agreement
between the two is evidence about the *street network*, not about OSM's
editing history.

This is the strongest available answer to "how do you know your distances are
right?" in the viva: OSRM (OSM), Valhalla (OSM, different implementation) and
OneMap (national dataset, different provenance) all in one table.

Cost of using it
----------------
OneMap's routing API takes **one origin-destination pair per request** and has
no matrix endpoint, so a full re-run of all 101,423 pairs is not feasible
(~10^5 requests). That is fine: validation does not need the full set, it needs
a sample that spans the distance range. 300 pairs is enough to estimate the
systematic bias to well under a percentage point.

Usage
-----
    export ONEMAP_TOKEN='...'          # JWT from /api/auth/post/getToken
    python 06_validate_onemap.py --samples 300

The token is read from the environment only and is never written to disk or
into the output files. OneMap tokens are short-lived (the one used during
development expired after 72 h), so this script prints the remaining validity
before it starts.

Notes on the API
----------------
* `start` and `end` are **latitude,longitude** - the opposite order from OSRM.
* The response carries `route_summary.total_distance` in metres and
  `total_time` in seconds.
* A pair can legitimately return 404 ("no route found") when the endpoints are
  outside the pedestrian network; those are counted, not silently dropped.
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

ROUTE_URL = "https://www.onemap.gov.sg/api/public/routingsvc/route"
USER_AGENT = "ntu-datasys-project/1.0 (student coursework)"


def token_expiry(token: str) -> float | None:
    """Decode the JWT payload and return the exp claim as a unix timestamp."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, ValueError):
        return None


def onemap_walk(token: str,
                start: tuple[float, float],
                end: tuple[float, float],
                retries: int = 3) -> tuple[float | None, float | None, str]:
    """Return (distance_m, duration_s, status) for one walking pair.

    status is one of: "ok", "no_route", "unauthorised", "quota", "error:<code>".
    """
    query = urllib.parse.urlencode({
        "start": f"{start[1]:.7f},{start[0]:.7f}",   # OneMap wants lat,lon
        "end": f"{end[1]:.7f},{end[0]:.7f}",
        "routeType": "walk",
    })
    url = f"{ROUTE_URL}?{query}"
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
                return None, None, "no_route"
            if exc.code == 401:
                return None, None, "unauthorised"
            if exc.code == 429:
                # Back off hard: the quota is undocumented, so treat it as a
                # signal to slow down rather than to retry immediately.
                time.sleep(15 * (attempt + 1))
                last_error = "quota"
                continue
            last_error = f"error:{exc.code}"
            time.sleep(2 ** attempt)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = f"error:{type(exc).__name__}"
            time.sleep(2 ** attempt)
    return None, None, last_error


def main() -> int:
    here = Path(__file__).resolve().parents[2]  # repo root (scripts/hawker_geo -> scripts -> .)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--walk", default=str(here / "data" / "hawker_geo" / "hdb_hawker_walk.csv"))
    parser.add_argument("--hawker", default=str(here / "data" / "hawker_geo" / "hawker_centre.csv"))
    parser.add_argument("--outdir", default=str(here / "data" / "hawker_geo"))
    parser.add_argument("--cache", default=str(here / "data" / "hawker_geo" / "onemap_cache.jsonl"))
    parser.add_argument("--samples", type=int, default=300)
    parser.add_argument("--buckets", type=int, default=6,
                        help="distance strata to sample from evenly")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--delay", type=float, default=0.2,
                        help="seconds between requests")
    parser.add_argument("--token-env", default="ONEMAP_TOKEN")
    args = parser.parse_args()

    token = os.environ.get(args.token_env, "").strip()
    if not token:
        print(f"error: set {args.token_env} to a OneMap API token.")
        print("  register: https://www.onemap.gov.sg/apidocs/register")
        print("  token:    POST https://www.onemap.gov.sg/api/auth/post/getToken")
        return 2

    expires = token_expiry(token)
    if expires is not None:
        remaining_h = (expires - time.time()) / 3600
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(expires))
        print(f"[token] expires {stamp} ({remaining_h:+.1f} h from now)")
        if remaining_h <= 0:
            print("error: token has already expired - fetch a new one.")
            return 2

    # ---------------- load coordinates ---------------- #
    with Path(args.hawker).open(encoding="utf-8-sig") as handle:
        hawker_rows = list(csv.DictReader(handle))
    hawker_coords = {
        r["hawker_id"]: (float(r["longitude"]), float(r["latitude"]))
        for r in hawker_rows
    }

    # hdb_hawker_walk.csv carries no coordinates, only the routed distance, so
    # the block side has to be read from the block table.
    with (Path(args.outdir) / "hdb_block.csv").open(encoding="utf-8-sig") as handle:
        block_rows = list(csv.DictReader(handle))
    block_coords = {
        r["block_id"]: (float(r["longitude"]), float(r["latitude"]))
        for r in block_rows
    }

    with Path(args.walk).open(encoding="utf-8") as handle:
        pairs = list(csv.DictReader(handle))
    if not pairs:
        print("no routed pairs found - run 02_compute_walk_distance.py first")
        return 1

    # ---------------- stratified sample ---------------- #
    # Uniform random sampling would be dominated by 2-3 km pairs, because that
    # is where most of the mass sits. Sampling evenly by distance keeps the
    # short walks (where the class boundaries actually are) represented.
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

    # ---------------- resume cache ---------------- #
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
                    continue          # a killed run can leave a partial line
                done[(record["block_id"], record["hawker_id"])] = record
        print(f"[cache] {len(done)} pairs already fetched")

    todo = [r for r in sample if (r["block_id"], r["hawker_id"]) not in done]
    print(f"[fetch] {len(todo)} to go (delay {args.delay}s)")

    fetched = 0
    with cache_path.open("a", encoding="utf-8") as handle:
        for index, row in enumerate(todo, 1):
            block_id, hawker_id = row["block_id"], row["hawker_id"]
            if block_id not in block_coords or hawker_id not in hawker_coords:
                continue
            distance, duration, status = onemap_walk(
                token, block_coords[block_id], hawker_coords[hawker_id]
            )
            if status == "unauthorised":
                print("error: token rejected (401) - it may have expired.")
                break
            record = {
                "block_id": block_id,
                "hawker_id": hawker_id,
                "osrm_m": float(row["walk_distance_m"]),
                "onemap_m": distance,
                "onemap_s": duration,
                "status": status,
            }
            handle.write(json.dumps(record) + "\n")
            handle.flush()
            done[(block_id, hawker_id)] = record
            fetched += 1
            if index % 25 == 0 or index == len(todo):
                print(f"    {index}/{len(todo)}  ({status})")
            time.sleep(args.delay)

    if fetched == 0 and not done:
        print("nothing fetched - validation inconclusive")
        return 1

    # ---------------- compare ---------------- #
    results = []
    for row in sample:
        record = done.get((row["block_id"], row["hawker_id"]))
        if not record or record["status"] != "ok" or record["onemap_m"] is None:
            continue
        ours = record["osrm_m"]
        theirs = record["onemap_m"]
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

    if not results:
        print("no comparable pairs - validation inconclusive")
        return 1

    out_path = Path(args.outdir) / "onemap_validation.csv"
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    rel = [abs(r["rel_diff_pct"]) for r in results]
    signed = [r["rel_diff_pct"] for r in results]
    print("\n" + "=" * 66)
    print("OSRM vs ONEMAP (SLA) WALKING DISTANCE AGREEMENT")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
