#!/usr/bin/env python3
"""
01_clean_hawker_geo.py - clean the raw hawker_geo.csv export into the
DB-ready HAWKER_CENTRE table, joining the official data.gov.sg register for
the stall counts Q5 needs.

The geo file (129 rows) is authoritative for locations, the official register
(107 rows) for attributes. All 129 rows are kept; the 22 without an official
record get NULL stall counts (opened after the register's snapshot), so Q5's
`no_of_mkt_produce_stalls > 0` predicate excludes them instead of silently
treating unknown as "no fresh market".
"""

from __future__ import annotations

import csv
import json
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]          # repo root
RAW_CSV = HERE / "data" / "raw_data" / "hawker_geo.csv"
OFFICIAL_CACHE = HERE / "data" / "raw_data" / "hawker_official_raw.json"
OUTDIR = HERE / "data" / "hawker_geo"

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


def main() -> int:
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
    # Row order defines hawker_id, and 02/03 rely on it matching the Hxxx
    # numbering inside the routing caches.
    fieldnames = [
        "hawker_id", "hawker_name", "street", "latitude", "longitude", "postal",
        "no_of_mkt_produce_stalls", "no_of_cooked_food_stalls",
    ]
    output_path = OUTDIR / "hawker_centre.csv"
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


if __name__ == "__main__":
    sys.exit(main())
