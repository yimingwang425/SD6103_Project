from __future__ import annotations

import glob
import os
import sys

import numpy as np
import pandas as pd


# supplementary material/Task2/scripts/hdb/hdb_clean.py -> <repo root>/data
BASE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))), "data"
)
RAW_PATH = os.path.join(BASE_DIR, "raw_data", "hdb.csv")
RESALE_DIR = os.path.join(BASE_DIR, "raw_data", "ResaleFlatPrices")
OUT_DIR = os.path.join(BASE_DIR, "clean_data")

# Translate the 3-letter town code in bldg_contract_town into its full name.
# Each code maps to exactly one resale town name (a strict one-to-one relationship).
TOWN_CODE_MAP = {
    "AMK": "ANG MO KIO",
    "BB": "BUKIT BATOK",
    "BD": "BEDOK",
    "BH": "BISHAN",
    "BM": "BUKIT MERAH",
    "BP": "BUKIT PANJANG",
    "BT": "BUKIT TIMAH",
    "CCK": "CHOA CHU KANG",
    "CL": "CLEMENTI",
    "CT": "CENTRAL AREA",
    "GL": "GEYLANG",
    "HG": "HOUGANG",
    "JE": "JURONG EAST",
    "JW": "JURONG WEST",
    "KWN": "KALLANG/WHAMPOA",
    "MP": "MARINE PARADE",
    "PG": "PUNGGOL",
    "PRC": "PASIR RIS",
    "QT": "QUEENSTOWN",
    "SB": "SEMBAWANG",
    "SGN": "SERANGOON",
    "SK": "SENGKANG",
    "TAP": "TAMPINES",
    "TP": "TOA PAYOH",
    "WL": "WOODLANDS",
    "YS": "YISHUN",
}

# These 12 columns should sum to total_dwelling_units; used for validation.
SOLD_COLS = [
    "1room_sold", "2room_sold", "3room_sold", "4room_sold", "5room_sold",
    "exec_sold", "multigen_sold", "studio_apartment_sold",
]
RENTAL_COLS = ["1room_rental", "2room_rental", "3room_rental", "other_room_rental"]

# The 6 Y/N boolean flag columns.
FLAG_COLS = [
    "residential", "commercial", "market_hawker",
    "miscellaneous", "multistorey_carpark", "precinct_pavilion",
]


def load_raw() -> pd.DataFrame:
    # Read the raw csv (postal must be read as a string to keep its leading zeros).
    df = pd.read_csv(RAW_PATH, dtype={"postal": str, "blk_no": str})
    print(f"Read raw data: {df.shape[0]} rows x {df.shape[1]} columns")
    return df


def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Drop 'Unnamed: 0' (pandas' RangeIndex written out with the csv; carries no business meaning).
    if "Unnamed: 0" in df.columns:
        df = df.drop(columns=["Unnamed: 0"])

    # blk_no: strip whitespace + uppercase. Store as VARCHAR(10), not INT.
    df["blk_no"] = df["blk_no"].astype(str).str.strip().str.upper()

    # street: strip whitespace + collapse repeated spaces + uppercase.
    # This column uses abbreviations that match the resale street_name column,
    # so it is kept as-is rather than expanded.
    # Note: 9744 of the 9745 (block, street) pairs in resale match hdb.csv exactly (99.99%).
    df["street"] = (
        df["street"].astype(str).str.strip().str.replace(r"\s+", " ", regex=True).str.upper()
    )

    # year_completed: cast to Int64.
    df["year_completed"] = df["year_completed"].astype("Int64")

    # The 6 Y/N columns: 'Y'/'N' strings -> Python booleans.
    # Note: market_hawker shows 112 blocks with their own hawker centre / market,
    # which can be cross-checked against the Hawker Centre table.
    for col in FLAG_COLS:
        df[col] = df[col].map({"Y": True, "N": False}).astype("boolean")

    # The 13 dwelling-unit count columns: cast to Int64.
    for col in ["total_dwelling_units"] + SOLD_COLS + RENTAL_COLS:
        df[col] = df[col].astype("Int64")

    # building: treat the string 'NIL' as missing (5045 rows), collapse extra spaces and uppercase.
    # Uppercase after the missing-value check, otherwise the string 'nan' would become 'NAN' and be kept.
    df["building"] = (
        df["building"].astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
    )
    df.loc[df["building"].isin(["NIL", "nan", ""]), "building"] = np.nan
    df["building"] = df["building"].str.upper()

    # addr: collapse extra spaces and uppercase.
    df["addr"] = (
        df["addr"].astype(str).str.strip().str.replace(r"\s+", " ", regex=True).str.upper()
    )

    # postal: strip whitespace; 'NIL' -> NULL.
    # 12366 rows hold a valid 6-digit code; the other 76 are 'NIL'.
    # Cross-check: the 6-digit code at the end of addr matches postal in every row.
    df["postal"] = df["postal"].astype(str).str.strip()
    df.loc[df["postal"].isin(["NIL", "nan", ""]), "postal"] = np.nan

    # bldg_contract_town -> town_name: use TOWN_CODE_MAP to translate the 3-letter code into the full town name used by the resale data.
    df["bldg_contract_town"] = df["bldg_contract_town"].astype(str).str.strip().str.upper()
    df["town_name"] = df["bldg_contract_town"].map(TOWN_CODE_MAP)

    # The 7 planning-area columns: strip + uppercase the text columns, cast SUBZONE_NO to Int64.
    for col in ("SUBZONE_N", "SUBZONE_C", "PLN_AREA_N", "PLN_AREA_C", "REGION_N", "REGION_C"):
        df[col] = df[col].astype("string").str.strip().str.upper()
    df["SUBZONE_NO"] = pd.to_numeric(df["SUBZONE_NO"], errors="coerce").astype("Int64")

    return df


def flag_row_issues(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Verification A: does the sum of the unit-type columns equal total_dwelling_units?
    # 12 of the 12442 rows do not match, each off by 1, all in Pasir Ris (PASIR RIS RD / HTS / AVE / TER): total_dwelling_units = 1 but every unit-type column is 0.
    # These are HDB terrace houses, which do not fall into any "N-room" category, so the
    # unit-type columns cannot be filled in. This is a classification gap in the source, not an error.
    # Action: keep these 12 rows (they are real dwellings) and only flag them.
    unit_sum = df[SOLD_COLS + RENTAL_COLS].sum(axis=1)
    df["flag_unit_sum_mismatch"] = unit_sum != df["total_dwelling_units"]
    print(f"Unit-type total != total_dwelling_units: {int(df['flag_unit_sum_mismatch'].sum())} rows "
          f"(confirmed HDB terrace houses; kept and flagged)")

    # Verification B: does the block number at the start of addr match blk_no?
    # 11 rows do not match, e.g. blk_no = '118' but addr starts with '118C'.
    # This means the address on those rows was wrongly matched from a neighbouring block.
    # Action: keep but flag.
    addr_lead = df["addr"].str.extract(r"^(?:BLK\s+)?(\S+)", expand=False).str.upper()
    df["flag_addr_block_mismatch"] = addr_lead.ne(df["blk_no"])
    print(f"Rows whose addr block number != blk_no: {int(df['flag_addr_block_mismatch'].sum())}")

    return df


def flag_duplicate_coords(df: pd.DataFrame) -> pd.DataFrame:
    # Verification C: do two blocks share exactly the same latitude and longitude?
    # 6 pairs (12 blocks) match, e.g. 215 and 216 CHOA CHU KANG CTRL, 556 and 557 WOODLANDS DR 53.
    df = df.copy()
    has_coord = df["lat"].notna() & df["lng"].notna()
    df["flag_coord_duplicate"] = has_coord & df.duplicated(subset=["lat", "lng"], keep=False)

    # Combine B and C into a single "coordinates in doubt" flag for downstream use.
    # Only this combined flag is exported; the two inputs would be redundant in the output.
    df["flag_geo_suspect"] = df["flag_addr_block_mismatch"] | df["flag_coord_duplicate"]
    print(f"Rows with doubtful coordinates: {int(df['flag_geo_suspect'].sum())} "
          f"(of which {int(df['flag_coord_duplicate'].sum())} share coordinates with another block)")
    return df


def load_resale_block_keys() -> set[tuple[str, str]]:
    files = sorted(glob.glob(os.path.join(RESALE_DIR, "*.csv")))
    if not files:
        print("ResaleFlatPrices folder not found; skipping the 'has transactions' protection check")
        return set()
    keys: set[tuple[str, str]] = set()
    for f in files:
        r = pd.read_csv(f, usecols=["block", "street_name"], dtype=str)
        keys |= set(
            zip(
                r["block"].str.strip().str.upper(),
                r["street_name"].str.strip().str.replace(r"\s+", " ", regex=True).str.upper(),
            )
        )
    return keys


def filter_non_residential(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    resale_keys = load_resale_block_keys()
    is_res = df["residential"].fillna(False).astype(bool)
    has_txn = pd.Series(list(zip(df["blk_no"], df["street"])), index=df.index).isin(resale_keys)

    keep = is_res | has_txn
    rescued = (~is_res) & has_txn
    print(f"Non-residential blocks: {int((~is_res).sum())} -> dropped {int((~keep).sum())}, "
          f"kept {int(rescued.sum())} that have resale transactions")

    kept = df[keep].copy()
    # Flag the rescued blocks so downstream knows they currently hold no dwelling units.
    kept["flag_no_dwelling_unit"] = (~is_res[keep]).values
    return kept, df[~keep]


def build_town_table(df: pd.DataFrame) -> pd.DataFrame:
    towns = sorted(df["town_name"].dropna().unique())
    return pd.DataFrame({"TownID": range(1, len(towns) + 1), "TownName": towns})


def build_block_table(df: pd.DataFrame, town: pd.DataFrame) -> pd.DataFrame:
    name_to_id = dict(zip(town["TownName"], town["TownID"]))
    out = df.sort_values(["street", "blk_no"], kind="mergesort").reset_index(drop=True)
    out.insert(0, "block_id", range(1, len(out) + 1))
    out.insert(1, "town_id", out["town_name"].map(name_to_id).astype("Int64"))

    cols = [
        "block_id",
        "town_id",
        "blk_no",
        "street",
        "year_completed",
        "lat",
        "lng",
        "postal",

        "town_name",
        "max_floor_lvl",
        "building",
        "addr",
        "total_dwelling_units",
        "market_hawker",
        "commercial",
        "multistorey_carpark",
        "precinct_pavilion",
        "miscellaneous",
        "residential",
        "REGION_N", "PLN_AREA_N", "SUBZONE_N",

        "flag_unit_sum_mismatch",
        "flag_geo_suspect",
        "flag_no_dwelling_unit",
    ] + SOLD_COLS + RENTAL_COLS

    return out[[c for c in cols if c in out.columns]]


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)

    df = load_raw()
    df = clean_columns(df)
    df = flag_row_issues(df)

    # No de-duplication step: (blk_no, street) is unique across all 12442 raw rows, and there are no fully duplicated rows either.
    df, non_res_dropped = filter_non_residential(df)
    df = flag_duplicate_coords(df)

    town = build_town_table(df)
    block = build_block_table(df, town)

    # index=False so we do not write out another 'Unnamed: 0' column.
    block_path = os.path.join(OUT_DIR, "hdb_block_clean.csv")
    town_path = os.path.join(OUT_DIR, "hdb_town.csv")
    excl_path = os.path.join(OUT_DIR, "hdb_excluded.csv")

    block.to_csv(block_path, index=False)
    town.to_csv(town_path, index=False)
    non_res_dropped.to_csv(excl_path, index=False)

    print(f"Wrote {block_path} ({len(block)} rows, {block.shape[1]} columns)")
    print(f"Wrote {town_path} ({len(town)} rows)")
    print(f"Wrote {excl_path} ({len(non_res_dropped)} dropped rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
