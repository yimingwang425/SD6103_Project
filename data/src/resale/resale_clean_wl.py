import pandas as pd
import glob
import os
import re

# ── Paths ──
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW_DIR = os.path.join(BASE_DIR, "raw_data", "ResaleFlatPrices")
OUTPUT_PATH = os.path.join(BASE_DIR, "processed_data", "resale", "resale_cleaned_wl.csv")
REMOVED_PATH = os.path.join(BASE_DIR, "processed_data", "resale", "resale_removed_wl.csv")

# ── Step 1: Load & merge all CSVs ──
csv_files = sorted(glob.glob(os.path.join(RAW_DIR, "*.csv")))
dfs = [pd.read_csv(f) for f in csv_files]
df = pd.concat(dfs, ignore_index=True)
print(f"Step 1 - Merged: {len(df)} rows from {len(csv_files)} files")

# Collect all removed rows
removed = []

# ── Step 2: No deduplication ──
# All-column duplicates exist (1,929 rows), but they may be legitimate
# separate transactions (e.g. two identical flats sold at the same price
# in the same month). Since there is no unique transaction ID in the raw
# data, we keep all rows and rely on auto-increment resale_id as PK.
print(f"Step 2 - Skipped dedup: {df.duplicated().sum()} all-column duplicate rows kept")

# ── Step 3: Standardise flat_type ──
df["flat_type"] = df["flat_type"].replace("MULTI GENERATION", "MULTI-GENERATION")
print(f"Step 3 - flat_type unique values: {sorted(df['flat_type'].unique())}")

# ── Step 4: Standardise flat_model case ──
df["flat_model"] = df["flat_model"].str.title()
# Fix special cases after title-casing
df["flat_model"] = df["flat_model"].replace({
    "Dbss": "DBSS",
    "3Gen": "3Gen",
})
print(f"Step 4 - flat_model unique values ({df['flat_model'].nunique()}): {sorted(df['flat_model'].unique())}")

# ── Step 5: Standardise remaining_lease → remaining_lease_years ──
def parse_remaining_lease(val, month_str, lease_commence):
    """Convert remaining_lease to years (int)."""
    if pd.isna(val):
        txn_year = int(month_str[:4])
        return 99 - txn_year + int(lease_commence)
    val_str = str(val)
    if val_str.isdigit():
        return int(val_str)
    # Format: "61 years 04 months" → take years, round up if months > 0
    match = re.match(r"(\d+)\s+years?(?:\s+(\d+)\s+months?)?", val_str)
    if match:
        return int(match.group(1))
    return None

df["remaining_lease_years"] = df.apply(
    lambda row: parse_remaining_lease(row["remaining_lease"], row["month"], row["lease_commence_date"]),
    axis=1,
)
df["remaining_lease_years"] = df["remaining_lease_years"].astype(int)
df = df.drop(columns=["remaining_lease"])
# Remove rows where remaining_lease_years > 99 (data error: 99-year lease cannot exceed 99)
invalid = df["remaining_lease_years"] > 99
removed.append(df[invalid].assign(removal_reason="remaining_lease > 99"))
print(f"Step 5 - Removed {invalid.sum()} rows with remaining_lease_years > 99 (data error)")
df = df[~invalid]
print(f"Step 5 - remaining_lease_years range: {df['remaining_lease_years'].min()} ~ {df['remaining_lease_years'].max()}")

# ── Step 6: Validate month format ──
# Keep as YYYY-MM string; verify all values parse correctly
pd.to_datetime(df["month"], format="%Y-%m")
print(f"Step 6 - month range: {df['month'].min()} ~ {df['month'].max()}")

# ── Step 7: Split storey_range into storey_min and storey_max ──
df[["storey_min", "storey_max"]] = df["storey_range"].str.split(" TO ", expand=True).astype(int)
df = df.drop(columns=["storey_range"])
print(f"Step 7 - storey_min: {df['storey_min'].min()}~{df['storey_min'].max()}, storey_max: {df['storey_max'].min()}~{df['storey_max'].max()}")

# ── Step 8: Numeric type enforcement ──
df["resale_price"] = df["resale_price"].astype(int)
df["lease_commence_date"] = df["lease_commence_date"].astype(int)
# floor_area_sqm stays as float (has decimal values like 366.7)

# ── Step 9: String columns — already clean (uppercase), ensure str type for block ──
df["block"] = df["block"].astype(str)

# ── Reorder columns ──
df = df[
    [
        "month",
        "town",
        "flat_type",
        "block",
        "street_name",
        "storey_min",
        "storey_max",
        "floor_area_sqm",
        "flat_model",
        "lease_commence_date",
        "remaining_lease_years",
        "resale_price",
    ]
]

# ── Output ──
df.to_csv(OUTPUT_PATH, index=False)
removed_df = pd.concat(removed, ignore_index=True)
removed_df.to_csv(REMOVED_PATH, index=False)
print(f"\nDone! Saved {len(df)} rows to {OUTPUT_PATH}")
print(f"Removed {len(removed_df)} rows to {REMOVED_PATH}")
