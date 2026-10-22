#!/usr/bin/env python3
"""
01_clean_hawker_geo.py
======================
Data Systems Project - Hawker-geo workstream (Task 2.1 data acquisition).

Takes the raw `hawker_geo.csv` export (extracted from the data.gov.sg
"Hawker Centres (GEOJSON)" dataset) and turns it into a DB-ready table for the
`hawker_centre` entity.

What this script does
---------------------
1. Structural validation   - coordinate parsing, Singapore bounding box, precision.
2. Text normalisation      - non-breaking spaces, missing spaces after commas,
                             parenthetical aliases split into their own column.
3. Address parsing         - postal code extraction (the reliable join key).
4. Enrichment              - join to the official data.gov.sg "Hawker Centres"
                             dataset (107 rows) to obtain stall counts, in
                             particular `no_of_mkt_produce_stalls`, which is what
                             Query Q5 ("hawker with fresh market stalls") needs.
5. Join strategy           - postal code first (exact, verified one-to-one),
                             then a small explicit alias table for centres that
                             were renamed between the two source snapshots, then
                             a *strict* fuzzy fallback that cannot steal an
                             already-claimed official record.
6. Output                  - output/hawker_centre.csv + a match audit report.

Design decisions
----------------
* The geo file (129 rows) is authoritative for *locations*; the official
  dataset (107 rows) is authoritative for *attributes*. We keep all 129 rows and
  LEFT JOIN the attributes. Centres with no official record get NULL stall
  counts (they are all hawker centres opened after the official snapshot was
  taken), so Q5's `no_of_mkt_produce_stalls > 0` predicate excludes them rather
  than silently treating them as "no fresh market".
* The join is enforced to be one-to-one. Two geo rows describe the same site as
  one official row only when they really are the same physical facility; the
  *interim* / *replacement* centres are deliberately NOT merged.
* Missing addresses are left NULL rather than fabricated.

Usage
-----
    python 01_clean_hawker_geo.py
    python 01_clean_hawker_geo.py --raw "hawker_geo (1).csv" --outdir output
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import urllib.request
from pathlib import Path

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

OFFICIAL_RESOURCE_ID = "d_68a42f09f350881996d83f9cd73ab02f"
OFFICIAL_API = (
    "https://data.gov.sg/api/action/datastore_search"
    f"?resource_id={OFFICIAL_RESOURCE_ID}&limit=500"
)

# Singapore mainland + islands bounding box. Anything outside is a corrupt point.
SG_BBOX = {"lat_min": 1.130, "lat_max": 1.500, "lon_min": 103.550, "lon_max": 104.150}

# Centres renamed between the official snapshot and the GeoJSON snapshot. Their
# postal codes differ too, so neither postal nor fuzzy matching can recover them.
#   geo name (lower-cased, as it appears in the raw file) -> official name
MANUAL_ALIASES = {
    "market street hawker centre": "Market Street Food Centre",
    "tanglin halt hawker centre & market": "Blks 1A/ 2A/ 3A Commonwealth Drive",
}

# Deliberately minimal: only tokens that carry no identifying information.
# Notably we do NOT strip "interim", "market" or "hawker", because doing so
# collapses genuinely distinct facilities onto each other (e.g. the interim
# Bukit Timah hawker centre vs the rebuilt Bukit Timah Market).
STOPWORDS = {"the", "and", "of", "blk", "block", "blks", "blocks"}

FUZZY_THRESHOLD = 0.80


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def clean_text(value: str | None) -> str:
    """Normalise whitespace artefacts present in the raw export."""
    if value is None:
        return ""
    # The raw file contains U+00A0 (non-breaking space) inside addresses,
    # e.g. "Singapore\xa0738623".
    text = value.replace("\u00a0", " ").replace("\u200b", "")
    text = text.strip()
    # Some addresses are missing the space after the comma: "Blk 4,Woodlands".
    text = re.sub(r"\s*,\s*", ", ", text)
    # ... and the space before "Singapore": "St 12,Singapore 738623".
    text = re.sub(r"(?<=[,\w])Singapore\b", " Singapore", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" ,")


def split_name(raw_name: str) -> tuple[str, str]:
    """
    Split "Blk 51 Old Airport Road (51 Old Airport Road Food Centre)" into
    ("Blk 51 Old Airport Road", "51 Old Airport Road Food Centre").

    The bracketed part is the colloquial/official name and is kept as `alias`;
    the part before the bracket is the block+street descriptor.
    """
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
    # Official dataset uses "S(289876)"; geo file uses "Singapore 289876".
    for pattern in (r"S\((\d{6})\)", r"\b(\d{6})\b"):
        match = re.search(pattern, address)
        if match:
            return match.group(1)
    return ""


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
    """Parse a coordinate, validate it against the Singapore bounding box."""
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
    return round(number, 6)  # ~0.11 m resolution, plenty for walking distances


def fetch_official(cache_path: Path) -> list[dict]:
    """Fetch the official hawker-centre dataset, with an on-disk cache."""
    if cache_path.exists():
        print(f"[official] using cache {cache_path}")
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        print(f"[official] downloading {OFFICIAL_RESOURCE_ID} ...")
        request = urllib.request.Request(
            OFFICIAL_API, headers={"User-Agent": "ntu-datasys-project/1.0"}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload), encoding="utf-8")

    records = payload["result"]["records"]
    print(f"[official] {len(records)} records (expected 107)")
    return records


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    here = Path(__file__).resolve().parents[2]  # repo root (scripts/hawker_geo -> scripts -> .)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", default=str(here / "data" / "raw_data" / "hawker_geo.csv"))
    parser.add_argument("--outdir", default=str(here / "data" / "hawker_geo"))
    args = parser.parse_args()

    raw_path = Path(args.raw)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    with raw_path.open(encoding="utf-8-sig") as handle:
        raw_rows = list(csv.DictReader(handle))
    print(f"[geo] read {len(raw_rows)} rows from {raw_path.name}")

    errors: list[str] = []

    # ---------------- 1. normalise the geo rows ---------------- #
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
        geo_rows.append(
            {
                "name": name,
                "alias": alias,
                "raw_name": clean_text(raw.get("NAME", "")),
                "address": address,
                "postal_code": extract_postal(address),
                "latitude": lat,
                "longitude": lon,
            }
        )

    # ---------------- 2. exact duplicate detection ---------------- #
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

    # ---------------- 3. join to the official dataset ---------------- #
    official = fetch_official(here / "data" / "raw_data" / "hawker_official_raw.json")
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

    # Pass 1 - postal code (exact and, as verified, one-to-one)
    for row in geo_rows:
        postal = row["postal_code"]
        if postal and postal in by_postal:
            record = by_postal[postal]
            if int(record["_id"]) not in claimed:
                row["official_record"] = record
                row["match_method"] = "postal"
                claimed.add(int(record["_id"]))

    # Pass 2 - explicit alias table for renamed centres
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

    # Pass 3 - strict fuzzy fallback, never reusing an already-claimed record.
    # In practice this matches nothing on this dataset; it exists so that a
    # future refresh of either snapshot does not silently lose rows.
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

    # ---------------- 4. project the matched attributes ---------------- #
    for row in geo_rows:
        record = row.pop("official_record")
        if record is None:
            # Unknown, not "no fresh market": leave NULL so Q5's
            # `no_of_mkt_produce_stalls > 0` filter excludes them explicitly.
            row.update(
                official_id="", official_name="", type_of_centre="", owner="",
                no_of_stalls="", no_of_cooked_food_stalls="",
                no_of_mkt_produce_stalls="", has_fresh_market="",
            )
            continue
        produce = int(record["no_of_mkt_produce_stalls"])
        row.update(
            official_id=record["_id"],
            official_name=record["name_of_centre"],
            type_of_centre=record["type_of_centre"],
            owner=record["owner"],
            no_of_stalls=record["no_of_stalls"],
            no_of_cooked_food_stalls=record["no_of_cooked_food_stalls"],
            no_of_mkt_produce_stalls=produce,
            # Q5 predicate: "hawker with fresh market stalls"
            has_fresh_market="true" if produce > 0 else "false",
        )

    # ---------------- 5. write the DB-ready table ---------------- #
    fieldnames = [
        "hawker_id", "name", "alias", "address", "postal_code",
        "latitude", "longitude", "type_of_centre", "owner",
        "no_of_stalls", "no_of_cooked_food_stalls", "no_of_mkt_produce_stalls",
        "has_fresh_market", "official_id", "match_method",
    ]
    output_path = outdir / "hawker_centre.csv"
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for index, row in enumerate(geo_rows, start=1):
            row["hawker_id"] = f"H{index:03d}"
            writer.writerow(row)

    # ---------------- 6. audit report ---------------- #
    report_path = outdir / "hawker_centre_match_report.csv"
    with report_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["hawker_id", "name", "postal_code", "official_id",
                        "official_name", "match_method", "address_missing"],
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in geo_rows:
            row["address_missing"] = "true" if not row["address"] else "false"
            writer.writerow(row)

    # ---------------- 7. console summary ---------------- #
    total = len(geo_rows)
    by_method: dict[str, int] = {}
    for row in geo_rows:
        bucket = row["match_method"].split("(")[0]
        by_method[bucket] = by_method.get(bucket, 0) + 1

    print("\n" + "=" * 66)
    print("HAWKER GEO CLEANING SUMMARY")
    print("=" * 66)
    print(f"rows in raw file                : {len(raw_rows)}")
    print(f"rows in cleaned table           : {total}")
    print(f"rows with a postal code         : {sum(1 for r in geo_rows if r['postal_code'])}")
    print(f"rows with a missing address     : {sum(1 for r in geo_rows if not r['address'])}")
    print(f"matched to official dataset     : {total - by_method.get('none', 0)}")
    for method, count in sorted(by_method.items()):
        print(f"    via {method:<22}: {count}")
    print(f"official centres not in geo file: {len(official) - len(claimed)}")
    print(f"centres with fresh market stalls: "
          f"{sum(1 for r in geo_rows if r['has_fresh_market'] == 'true')}"
          f"  (of {sum(1 for r in geo_rows if r['has_fresh_market'])} with known data)")
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
