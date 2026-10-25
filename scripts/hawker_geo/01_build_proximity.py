#!/usr/bin/env python3
"""
01_build_proximity.py - build the hawker-proximity dataset end to end, in
three independently re-runnable stages:

  clean   Clean the raw hawker_geo.csv export into the DB-ready
          HAWKER_CENTRE table (data/clean_data/hawker_centre.csv),
          joining the official data.gov.sg register for the stall counts
          Q5 needs. Seconds; uses the cached official download.

  walk    Compute real walking path distances between every HDB block and
          every nearby hawker centre with OSRM's foot-routing service
          (~37 min cold, seconds with the committed cache). Why not
          straight-line: great-circle distance cuts through buildings,
          canals and expressways; the measured detour factor is 1.2-1.6x
          - exactly the error that matters at the 500/1000 m thresholds.
          Resumable: every completed batch is appended to osrm_cache.jsonl
          and skipped on re-run.

  derive  Turn the routed pairs into the proximity tables
          (data/clean_data/proximity.csv + proximity_level.csv) and
          quantify how much the choice of distance metric matters.

Usage:
  python 01_build_proximity.py                          # all stages
  python 01_build_proximity.py --stage clean            # one stage
  python 01_build_proximity.py --stage walk --limit 300 # smoke test

Proximity definition (Task 1.1), walking distance:
  level 1  walk <= 500 m        (~7 min at 4.5 km/h)
  level 2  500 < walk <= 1000 m
  level 3  walk > 1000 m
A block's own level (what Q4 uses) is the level of its nearest centre.

The pipeline speaks legacy Bxxxxx/Hxxx ids internally (the routing caches
are keyed on them); the relation CSV carries natural keys instead:
(blk_no, street) for blocks and hawker_name for centres, which is what
the database resolves surrogate ids on at load time (sp06-sp08).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]          # repo root
CLEAN_DIR = HERE / "data" / "clean_data"
OUTDIR = HERE / "data" / "hawker_geo"
HDB_CSV = HERE / "data" / "raw_data" / "hdb.csv"
HAWKER_CSV = CLEAN_DIR / "hawker_centre.csv"
WALK_CSV = OUTDIR / "hdb_hawker_walk.csv"
# to build the OneMap variant instead, point WALK_CSV at
# hdb_hawker_walk_onemap.csv (produced by 02_validate_distances.py --full)

# ---------------------------------------------------------------- clean --
RAW_CSV = HERE / "data" / "raw_data" / "hawker_geo.csv"
OFFICIAL_CACHE = HERE / "data" / "raw_data" / "hawker_official_raw.json"

OFFICIAL_RESOURCE_ID = "d_68a42f09f350881996d83f9cd73ab02f"
OFFICIAL_API = (
    "https://data.gov.sg/api/action/datastore_search"
    f"?resource_id={OFFICIAL_RESOURCE_ID}&limit=500"
)

# Singapore mainland + islands bounding box; anything outside is a corrupt point
SG_BBOX = {"lat_min": 1.130, "lat_max": 1.500, "lon_min": 103.550, "lon_max": 104.150}

# centres renamed between the two snapshots - their postal codes differ too,
# so neither postal nor fuzzy matching can recover them
MANUAL_ALIASES = {
    "market street hawker centre": "Market Street Food Centre",
    "tanglin halt hawker centre & market": "Blks 1A/ 2A/ 3A Commonwealth Drive",
}

# deliberately minimal: only tokens that carry no identifying information.
# "market"/"hawker" must NOT be stripped - doing so collapses genuinely
# distinct facilities onto each other (interim Bukit Timah vs Bukit Timah Market)
STOPWORDS = {"the", "and", "of", "blk", "block", "blks", "blocks"}

FUZZY_THRESHOLD = 0.80


def clean_text(value: str | None) -> str:
    """Normalise whitespace artefacts present in the raw export."""
    if value is None:
        return ""
    # U+00A0 (non-breaking space) inside addresses, e.g. "Singapore\xa0738623"
    text = value.replace("\u00a0", " ").replace("\u200b", "")
    text = text.strip()
    text = re.sub(r"\s*,\s*", ", ", text)            # "Blk 4,Woodlands"
    text = re.sub(r"(?<=[,\w])Singapore\b", " Singapore", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" ,")


def split_name(raw_name: str) -> tuple[str, str]:
    """Split "Blk 51 Old Airport Road (51 Old Airport Road Food Centre)" into
    ("Blk 51 Old Airport Road", "51 Old Airport Road Food Centre"): the
    bracketed part is the colloquial name and is kept as `alias`."""
    raw_name = clean_text(raw_name)
    match = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", raw_name)
    if match:
        primary, alias = match.group(1).strip(), match.group(2).strip()
        if primary:
            return primary, alias
        return alias, primary
    return raw_name, ""


def extract_postal(address: str) -> str:
    """Return the 6-digit Singapore postal code, or '' if absent."""
    if not address:
        return ""
    # official dataset uses "S(289876)"; geo file uses "Singapore 289876"
    for pattern in (r"S\((\d{6})\)", r"\b(\d{6})\b"):
        match = re.search(pattern, address)
        if match:
            return match.group(1)
    return ""


def split_street(address: str) -> str:
    """'31, Commonwealth Crescent, Singapore 149644' -> '31, Commonwealth Crescent'."""
    text = re.sub(r"\s*,?\s*S\(\d{6}\)\s*$", "", address)
    text = re.sub(r"\s*,?\s*Singapore\s+\d{6}\s*$", "", text)
    return text.strip(" ,")


def name_tokens(name: str) -> set[str]:
    """Token set used for fuzzy comparison."""
    lowered = name.lower().replace("&", " and ")
    lowered = re.sub(r"[^a-z0-9 ]", " ", lowered)
    return {t for t in lowered.split() if t and t not in STOPWORDS}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def parse_coord(value: str, kind: str, row_label: str, errors: list[str]) -> float | None:
    """Parse a coordinate and validate it against the Singapore bounding box."""
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        errors.append(f"{row_label}: {kind} is not numeric ({value!r})")
        return None
    low, high = (SG_BBOX["lat_min"], SG_BBOX["lat_max"]) if kind == "latitude" \
        else (SG_BBOX["lon_min"], SG_BBOX["lon_max"])
    if not (low < number < high):
        errors.append(f"{row_label}: {kind} {number} outside Singapore bbox")
        return None
    return round(number, 6)      # ~0.11 m resolution, plenty for walking distances


def fetch_official(cache_path: Path) -> list[dict]:
    """Fetch the official hawker-centre dataset, with an on-disk cache."""
    if cache_path.exists():
        print(f"[official] using cache {cache_path.name}")
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        print(f"[official] downloading {OFFICIAL_RESOURCE_ID} ...")
        request = urllib.request.Request(OFFICIAL_API, headers={"User-Agent": "ntu-datasys-project/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload), encoding="utf-8")

    records = payload["result"]["records"]
    print(f"[official] {len(records)} records (expected 107)")
    return records


def clean_hawker_centres() -> int:
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    OUTDIR.mkdir(parents=True, exist_ok=True)

    with RAW_CSV.open(encoding="utf-8-sig") as handle:
        raw_rows = list(csv.DictReader(handle))
    print(f"[geo] read {len(raw_rows)} rows from {RAW_CSV.name}")

    errors: list[str] = []

    # normalise the geo rows
    geo_rows: list[dict] = []
    for index, raw in enumerate(raw_rows):
        label = f"geo[{index}] {raw.get('NAME', '?')!r}"
        lat = parse_coord(raw.get("latitude", ""), "latitude", label, errors)
        lon = parse_coord(raw.get("longitude", ""), "longitude", label, errors)
        if lat is None or lon is None:
            continue

        name, alias = split_name(raw.get("NAME", ""))
        if not name:
            errors.append(f"{label}: empty name after normalisation")
            continue

        address = clean_text(raw.get("ADDRESS_MYENV", ""))
        geo_rows.append({
            "name": name,
            "alias": alias,
            "raw_name": clean_text(raw.get("NAME", "")),
            "address": address,
            "postal_code": extract_postal(address),
            "latitude": lat,
            "longitude": lon,
        })

    # exact duplicate detection
    seen: dict[tuple[str, str], str] = {}
    duplicates: list[str] = []
    deduped: list[dict] = []
    for row in geo_rows:
        key = (row["name"].lower(), row["postal_code"])
        if key in seen:
            duplicates.append(f"{row['name']} duplicates {seen[key]}")
            continue
        seen[key] = row["name"]
        deduped.append(row)
    geo_rows = deduped
    print(f"[geo] {len(geo_rows)} valid rows after validation/dedup "
          f"({len(errors)} errors, {len(duplicates)} duplicates)")

    # join to the official dataset, three passes, each record claimed at most once
    official = fetch_official(OFFICIAL_CACHE)
    by_postal: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for record in official:
        postal = extract_postal(clean_text(record["location_of_centre"]))
        if postal:
            by_postal[postal] = record
        by_name[record["name_of_centre"].strip().lower()] = record

    claimed: set[int] = set()
    for row in geo_rows:
        row["match_method"] = "none"
        row["official_record"] = None

    # pass 1: postal code (exact and, as verified, one-to-one)
    for row in geo_rows:
        postal = row["postal_code"]
        if postal and postal in by_postal:
            record = by_postal[postal]
            if int(record["_id"]) not in claimed:
                row["official_record"] = record
                row["match_method"] = "postal"
                claimed.add(int(record["_id"]))

    # pass 2: explicit alias table for renamed centres
    for row in geo_rows:
        if row["official_record"] is not None:
            continue
        target = MANUAL_ALIASES.get(row["raw_name"].lower())
        if target and target.lower() in by_name:
            record = by_name[target.lower()]
            if int(record["_id"]) not in claimed:
                row["official_record"] = record
                row["match_method"] = "alias"
                claimed.add(int(record["_id"]))

    # pass 3: strict fuzzy fallback, never reusing an already-claimed record.
    # Matches nothing on this dataset; it exists so a future refresh of either
    # snapshot does not silently lose rows.
    for row in geo_rows:
        if row["official_record"] is not None:
            continue
        row_tokens = name_tokens(row["raw_name"]) | name_tokens(row["alias"])
        best, best_score = None, 0.0
        for record in official:
            if int(record["_id"]) in claimed:
                continue
            score = jaccard(row_tokens, name_tokens(record["name_of_centre"]))
            if score > best_score:
                best, best_score = record, score
        if best is not None and best_score >= FUZZY_THRESHOLD:
            row["official_record"] = best
            row["match_method"] = f"fuzzy({best_score:.2f})"
            claimed.add(int(best["_id"]))

    # project the matched attributes; unmatched centres keep NULL stall counts
    # (unknown, not "no fresh market") so Q5 excludes them explicitly
    for row in geo_rows:
        record = row.pop("official_record")
        if record is None:
            row.update(
                official_id="", official_name="", type_of_centre="", owner="",
                no_of_stalls="", no_of_cooked_food_stalls="",
                no_of_mkt_produce_stalls="",
            )
            continue
        row.update(
            official_id=record["_id"],
            official_name=record["name_of_centre"],
            type_of_centre=record["type_of_centre"],
            owner=record["owner"],
            no_of_stalls=record["no_of_stalls"],
            no_of_cooked_food_stalls=record["no_of_cooked_food_stalls"],
            no_of_mkt_produce_stalls=record["no_of_mkt_produce_stalls"],
        )

    # write the DB-ready table: exactly the HAWKER_CENTRE columns, in order.
    # Row order defines hawker_id, and walk/derive rely on it matching the Hxxx
    # numbering inside the routing caches.
    fieldnames = [
        "hawker_id", "hawker_name", "street", "latitude", "longitude", "postal",
        "no_of_mkt_produce_stalls", "no_of_cooked_food_stalls",
    ]
    output_path = CLEAN_DIR / "hawker_centre.csv"
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for index, row in enumerate(geo_rows, start=1):
            row["hawker_id"] = index
            row["hawker_name"] = row["name"]
            row["street"] = split_street(row["address"])
            row["postal"] = row["postal_code"]
            writer.writerow(row)

    # audit report (keeps the provenance columns the table does not carry)
    report_path = OUTDIR / "hawker_centre_match_report.csv"
    with report_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["hawker_id", "name", "postal_code", "official_id",
                        "official_name", "type_of_centre", "owner",
                        "no_of_mkt_produce_stalls", "match_method",
                        "address_missing"],
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in geo_rows:
            row["address_missing"] = "true" if not row["address"] else "false"
            writer.writerow(row)

    # console summary
    total = len(geo_rows)
    by_method: dict[str, int] = {}
    for row in geo_rows:
        bucket = row["match_method"].split("(")[0]
        by_method[bucket] = by_method.get(bucket, 0) + 1

    def known_stalls(row: dict) -> bool:
        return row["no_of_mkt_produce_stalls"] != ""

    with_market = sum(1 for r in geo_rows
                      if known_stalls(r) and int(r["no_of_mkt_produce_stalls"]) > 0)

    print(f"rows in cleaned table           : {total}")
    print(f"rows with a postal code         : {sum(1 for r in geo_rows if r['postal_code'])}")
    print(f"matched to official dataset     : {total - by_method.get('none', 0)}")
    for method, count in sorted(by_method.items()):
        print(f"    via {method:<22}: {count}")
    print(f"centres with fresh market stalls: {with_market} "
          f"(of {sum(1 for r in geo_rows if known_stalls(r))} with known data)")
    if errors:
        print(f"\nvalidation errors ({len(errors)}):")
        for message in errors:
            print("   !", message)
    if duplicates:
        print(f"\nduplicates removed ({len(duplicates)}):")
        for message in duplicates:
            print("   !", message)

    missing_official = [r for r in official if int(r["_id"]) not in claimed]
    if missing_official:
        print("\nofficial centres with no location in the geo file:")
        for record in missing_official:
            print(f"   - [{record['_id']}] {record['name_of_centre']} "
                  f"({record['location_of_centre']})")

    print(f"\nwrote {output_path}")
    print(f"wrote {report_path}")
    return 0


# ----------------------------------------------------------------- walk --
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


# --------------------------------------------------------------- derive --
CLASS1_MAX = 500.0
CLASS2_MAX = 1000.0
LEVEL_CATEGORY = {1: "NEAR", 2: "MID", 3: "FAR"}


def manhattan_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Axis-aligned distance using a local equirectangular projection."""
    mean_lat = math.radians((lat1 + lat2) / 2)
    dx = abs(lon2 - lon1) * 111_320 * math.cos(mean_lat)
    dy = abs(lat2 - lat1) * 110_574
    return dx + dy


def classify(distance_m: float) -> int:
    if distance_m <= CLASS1_MAX:
        return 1
    if distance_m <= CLASS2_MAX:
        return 2
    return 3


def load_hawker_names() -> dict[str, str]:
    """legacy 'H001' -> hawker_name. The clean stage writes
    hawker_centre.csv in geo-file order, which is exactly the order the
    Hxxx numbering was assigned in."""
    with HAWKER_CSV.open(encoding="utf-8-sig") as handle:
        return {f"H{index + 1:03d}": row["hawker_name"]
                for index, row in enumerate(csv.DictReader(handle))}


def derive_proximity() -> None:
    # coordinates for the comparison metrics
    with HDB_CSV.open(encoding="utf-8-sig") as handle:
        hdb_rows = list(csv.DictReader(handle))
    coords = {}
    is_residential = {}
    for index, row in enumerate(hdb_rows):
        block_id = f"B{index + 1:05d}"
        coords[block_id] = (float(row["lng"]), float(row["lat"]))
        # residential == 'N' is exactly the set of blocks with zero dwelling
        # units (2,261 of 12,442): pure commercial blocks that can never carry
        # a resale transaction. Reported separately so the level distribution
        # is not diluted by them.
        is_residential[block_id] = row["residential"].strip() == "Y"

    with HAWKER_CSV.open(encoding="utf-8-sig") as handle:
        hawker_rows = list(csv.DictReader(handle))
    # keyed by the legacy Hxxx id: that is what the routing pipeline speaks
    hawker_coords = {
        f"H{index + 1:03d}": (float(row["longitude"]), float(row["latitude"]))
        for index, row in enumerate(hawker_rows)
    }
    hawker_fresh = {
        f"H{index + 1:03d}": row["no_of_mkt_produce_stalls"] not in ("", None)
                             and int(row["no_of_mkt_produce_stalls"]) > 0
        for index, row in enumerate(hawker_rows)
    }
    hawker_name = load_hawker_names()

    # load the routed pairs
    with WALK_CSV.open(encoding="utf-8") as handle:
        pairs = list(csv.DictReader(handle))
    print(f"[load] {len(pairs)} routed block-hawker pairs")

    # classify and write the relation: natural keys, sp08 resolves the ids
    relation_path = CLEAN_DIR / "proximity.csv"
    with relation_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["blk_no", "street", "hawker_name",
                         "distance_m", "level_id"])
        for row in pairs:
            writer.writerow([
                row["blk_no"], row["street"], hawker_name[row["hawker_id"]],
                row["walk_distance_m"], classify(float(row["walk_distance_m"])),
            ])

    # the three level bands (min, max]: a boundary distance belongs to the
    # lower level - one pair in the data sits exactly on 1000.0 m
    level_path = CLEAN_DIR / "proximity_level.csv"
    with level_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["level_id", "category", "distance_min_m", "distance_max_m"])
        writer.writerow([1, LEVEL_CATEGORY[1], 0, int(CLASS1_MAX)])
        writer.writerow([2, LEVEL_CATEGORY[2], int(CLASS1_MAX), int(CLASS2_MAX)])
        writer.writerow([3, LEVEL_CATEGORY[3], int(CLASS2_MAX), ""])

    # nearest hawker per block, by each of the three metrics
    best_walk: dict[str, tuple[float, str]] = {}
    best_straight: dict[str, tuple[float, str]] = {}
    best_manhattan: dict[str, tuple[float, str]] = {}
    for row in pairs:
        block_id, hawker_id = row["block_id"], row["hawker_id"]
        walk = float(row["walk_distance_m"])
        block_lon, block_lat = coords[block_id]
        hawker_lon, hawker_lat = hawker_coords[hawker_id]
        straight = float(row["straight_line_m"])
        manhattan = manhattan_m(block_lon, block_lat, hawker_lon, hawker_lat)

        if block_id not in best_walk or walk < best_walk[block_id][0]:
            best_walk[block_id] = (walk, hawker_id)
        if block_id not in best_straight or straight < best_straight[block_id][0]:
            best_straight[block_id] = (straight, hawker_id)
        if block_id not in best_manhattan or manhattan < best_manhattan[block_id][0]:
            best_manhattan[block_id] = (manhattan, hawker_id)

    nearest_path = OUTDIR / "hdb_block_nearest.csv"
    nearest_level_counter: Counter[int] = Counter()
    with nearest_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "block_id", "blk_no", "street", "postal_code", "town",
            "nearest_hawker_id", "nearest_walk_m", "nearest_walk_min",
            "proximity_level", "level_category", "nearest_has_fresh_market",
        ])
        meta = {row["block_id"]: row for row in pairs}
        for block_id in sorted(best_walk):
            walk, hawker_id = best_walk[block_id]
            level = classify(walk)
            nearest_level_counter[level] += 1
            row = meta[block_id]
            writer.writerow([
                block_id, row["blk_no"], row["street"], row["postal_code"],
                row["town"], hawker_id, round(walk, 1), round(walk / 80, 1),
                level, LEVEL_CATEGORY[level],
                "true" if hawker_fresh[hawker_id] else "false",
            ])

    # how much does the metric matter?
    comparison_path = OUTDIR / "method_comparison.csv"
    rows_out = []
    for block_id in sorted(best_walk):
        walk_d, walk_h = best_walk[block_id]
        straight_d, straight_h = best_straight[block_id]
        manhattan_d, manhattan_h = best_manhattan[block_id]
        rows_out.append([
            block_id,
            walk_h, round(walk_d, 1), classify(walk_d),
            straight_h, round(straight_d, 1), classify(straight_d),
            manhattan_h, round(manhattan_d, 1), classify(manhattan_d),
        ])
    with comparison_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "block_id",
            "nearest_by_walk", "walk_m", "level_by_walk",
            "nearest_by_straight", "straight_m", "level_by_straight",
            "nearest_by_manhattan", "manhattan_m", "level_by_manhattan",
        ])
        writer.writerows(rows_out)

    different_centre_straight = sum(1 for r in rows_out if r[1] != r[4])
    different_centre_manhattan = sum(1 for r in rows_out if r[1] != r[7])
    level_diff_straight = sum(1 for r in rows_out if r[3] != r[6])
    level_diff_manhattan = sum(1 for r in rows_out if r[3] != r[9])

    # Ratios are only meaningful once the straight-line distance is well above
    # the routing snap tolerance. Some hawker centres sit inside the very HDB
    # block they serve (straight line ~0 m, walk 60-90 m); the ratio there is
    # pure noise, so such pairs are excluded from the ratio statistics.
    RATIO_MIN_M = 20.0
    ratios = [float(r["walk_distance_m"]) / float(r["straight_line_m"])
              for r in pairs if float(r["straight_line_m"]) >= RATIO_MIN_M]
    manhattan_ratios = []
    for row in pairs:
        block_lon, block_lat = coords[row["block_id"]]
        hawker_lon, hawker_lat = hawker_coords[row["hawker_id"]]
        manhattan = manhattan_m(block_lon, block_lat, hawker_lon, hawker_lat)
        if manhattan >= RATIO_MIN_M:
            manhattan_ratios.append(float(row["walk_distance_m"]) / manhattan)

    total_blocks = len(rows_out)

    # Is straight-line distance salvageable with better thresholds?
    #
    # Applying the *walking* thresholds (500/1000) to a straight-line distance
    # is not a fair test: straight lines are systematically shorter, so almost
    # everything shifts down a level. The fair question is whether there exist
    # straight-line thresholds that reproduce the walking levels well. We find
    # the optimal pair by exhaustive search over candidate cut points, which is
    # cheap because the correct-count can be read off prefix sums.
    straight_to_walk_nearest = {
        (row["block_id"], row["hawker_id"]): float(row["straight_line_m"])
        for row in pairs
    }
    manhattan_to_walk_nearest = {}
    for row in pairs:
        block_lon, block_lat = coords[row["block_id"]]
        hawker_lon, hawker_lat = hawker_coords[row["hawker_id"]]
        manhattan_to_walk_nearest[(row["block_id"], row["hawker_id"])] = \
            manhattan_m(block_lon, block_lat, hawker_lon, hawker_lat)

    def best_threshold_agreement(metric_of) -> tuple[float, float, float]:
        """Return (best accuracy %, t1, t2) for a 3-class split on `metric_of`."""
        samples = []
        for block_id, (walk_d, hawker_id) in best_walk.items():
            samples.append((metric_of[(block_id, hawker_id)], classify(walk_d)))
        samples.sort()
        n = len(samples)
        # prefix counts of the true class among the first i sorted samples
        c1, c2, c3 = [0], [0], [0]
        for _, truth in samples:
            c1.append(c1[-1] + (truth == 1))
            c2.append(c2[-1] + (truth == 2))
            c3.append(c3[-1] + (truth == 3))
        step = max(1, n // 200)
        cut = list(range(0, n + 1, step))
        if cut[-1] != n:
            cut.append(n)
        best_correct = -1
        best_t1 = best_t2 = 0.0
        for i in cut:                      # samples [0, i) predicted level 1
            for j in cut:                  # samples [i, j) predicted level 2
                if j < i:
                    continue
                correct = c1[i] + (c2[j] - c2[i]) + (c3[n] - c3[j])
                if correct > best_correct:
                    best_correct = correct
                    best_t1 = samples[i - 1][0] if i > 0 else 0.0
                    best_t2 = samples[j - 1][0] if j > 0 else 0.0
        return 100 * best_correct / n, best_t1, best_t2

    straight_best = best_threshold_agreement(straight_to_walk_nearest)
    manhattan_best = best_threshold_agreement(manhattan_to_walk_nearest)

    # detour factor of each block's nearest pair (the number that actually
    # decides the level), not of every candidate pair
    nearest_ratios = []
    for block_id, (walk_d, hawker_id) in best_walk.items():
        straight_d = straight_to_walk_nearest[(block_id, hawker_id)]
        if straight_d >= 20:   # below that the ratio is dominated by snapping
            nearest_ratios.append(walk_d / straight_d)
    nearest_ratios.sort()

    print("\nPROXIMITY LEVELS")
    print(f"level 1  walk <= {CLASS1_MAX:.0f} m : {nearest_level_counter[1]:5d} blocks "
          f"({100 * nearest_level_counter[1] / total_blocks:.1f}%)")
    print(f"level 2  {CLASS1_MAX:.0f}-{CLASS2_MAX:.0f} m     : "
          f"{nearest_level_counter[2]:5d} blocks "
          f"({100 * nearest_level_counter[2] / total_blocks:.1f}%)")
    print(f"level 3  walk >  {CLASS2_MAX:.0f} m : {nearest_level_counter[3]:5d} blocks "
          f"({100 * nearest_level_counter[3] / total_blocks:.1f}%)")
    print(f"total blocks with a hawker       : {total_blocks}")

    residential_counter: Counter[int] = Counter()
    for block_id, (walk_d, _) in best_walk.items():
        if is_residential[block_id]:
            residential_counter[classify(walk_d)] += 1
    residential_total = sum(residential_counter.values())
    print(f"\nrestricted to the {residential_total} residential blocks "
          f"(the ones that can appear in a resale transaction):")
    for level in (1, 2, 3):
        print(f"    level {level}: {residential_counter[level]:5d} "
              f"({100 * residential_counter[level] / residential_total:.1f}%)")

    print("\nHOW MUCH DOES THE METRIC MATTER?")
    print(f"pairs considered                 : {len(pairs)}")
    print(f"walk / straight-line   median    : {statistics.median(ratios):.3f}  "
          f"(mean {statistics.mean(ratios):.3f})")
    print(f"walk / manhattan       median    : "
          f"{statistics.median(manhattan_ratios):.3f}  "
          f"(mean {statistics.mean(manhattan_ratios):.3f})")
    print(f"pairs where walk > 1.5x straight : "
          f"{sum(1 for r in ratios if r > 1.5)} "
          f"({100 * sum(1 for r in ratios if r > 1.5) / len(ratios):.1f}%)")
    print()
    print("detour factor of each block's nearest centre:")
    print(f"    p5 / p50 / p95               : "
          f"{nearest_ratios[len(nearest_ratios) // 20]:.2f} / "
          f"{statistics.median(nearest_ratios):.2f} / "
          f"{nearest_ratios[int(len(nearest_ratios) * 0.95)]:.2f}")
    print(f"    blocks above 2.0x            : "
          f"{sum(1 for r in nearest_ratios if r > 2.0)} "
          f"({100 * sum(1 for r in nearest_ratios if r > 2.0) / len(nearest_ratios):.1f}%)")
    print()
    print("naive: reuse the walking thresholds on the cheap metric")
    print(f"    nearest centre differs, straight : {different_centre_straight} blocks "
          f"({100 * different_centre_straight / total_blocks:.1f}%)")
    print(f"    nearest centre differs, manhattan: {different_centre_manhattan} blocks "
          f"({100 * different_centre_manhattan / total_blocks:.1f}%)")
    print(f"    level differs, straight          : {level_diff_straight} blocks "
          f"({100 * level_diff_straight / total_blocks:.1f}%)")
    print(f"    level differs, manhattan         : {level_diff_manhattan} blocks "
          f"({100 * level_diff_manhattan / total_blocks:.1f}%)")
    print()
    print("fair: re-calibrate the cheap metric's thresholds to best fit")
    print(f"    straight-line  best agreement {straight_best[0]:.1f}% "
          f"at {straight_best[1]:.0f} / {straight_best[2]:.0f} m "
          f"-> {100 - straight_best[0]:.1f}% of blocks still misclassified")
    print(f"    manhattan      best agreement {manhattan_best[0]:.1f}% "
          f"at {manhattan_best[1]:.0f} / {manhattan_best[2]:.0f} m "
          f"-> {100 - manhattan_best[0]:.1f}% of blocks still misclassified")

    print(f"\nwrote {relation_path} ({len(pairs)} rows)")
    print(f"wrote {level_path}")
    print(f"wrote {nearest_path} ({total_blocks} rows)")
    print(f"wrote {comparison_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=["clean", "walk", "derive", "all"],
                        default="all", help="which stage to run (default: all)")
    parser.add_argument("--limit", type=int, default=0,
                        help="walk stage only: process the first N blocks "
                             "(smoke test)")
    args = parser.parse_args()

    if args.stage in ("clean", "all"):
        clean_hawker_centres()
    if args.stage in ("walk", "all"):
        compute_walk_distances(args.limit)
    if args.stage in ("derive", "all"):
        derive_proximity()
    return 0


if __name__ == "__main__":
    sys.exit(main())
