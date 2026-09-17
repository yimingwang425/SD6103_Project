# Resale Flat Prices - Data Cleaning

## Raw Data

5 CSV files from `data/raw_data/ResaleFlatPrices/`, covering 1990-01 to 2026-09, totalling 986,771 rows.

| File | Period | Rows | Has `remaining_lease` |
|------|--------|------|-----------------------|
| Resale Flat Prices (Based on Approval Date), 1990 - 1999 | 1990-1999 | 287,196 | No |
| Resale Flat Prices (Based on Approval Date), 2000 - Feb 2012 | 2000-2012 | 369,651 | No |
| Resale Flat Prices (Based on Registration Date), From Mar 2012 to Dec 2014 | 2012-2014 | 52,203 | No |
| Resale Flat Prices (Based on Registration Date), From Jan 2015 to Dec 2016 | 2015-2016 | 37,153 | Yes (integer) |
| ResaleflatpricesbasedonregistrationdatefromJan2017onwards | 2017-onwards | 240,568 | Yes (string) |

## Cleaning Steps

### 1. Merge

Concatenate all 5 CSVs. Files without `remaining_lease` column get NaN for that field.

### 2. Duplicates (kept)

1,929 rows are all-column duplicates, but they are **not removed**. Since the raw data has no unique transaction ID, these may be legitimate separate transactions (e.g. two identical flats in the same block sold at the same price in the same month). A surrogate `resale_id` (auto-increment) will serve as the primary key in the database.

### 3. Standardise `flat_type`

`MULTI GENERATION` (no hyphen, 279 rows) → `MULTI-GENERATION`, to match the other 275 rows.

Final values (7): `1 ROOM`, `2 ROOM`, `3 ROOM`, `4 ROOM`, `5 ROOM`, `EXECUTIVE`, `MULTI-GENERATION`

### 4. Standardise `flat_model` casing

Raw data has mixed casing (e.g. `IMPROVED` vs `Improved`), resulting in 34 distinct values for 21 actual models.

- Convert all to Title Case (`.str.title()`)
- Fix abbreviation: `Dbss` → `DBSS`

Final values (21): `2-Room`, `3Gen`, `Adjoined Flat`, `Apartment`, `DBSS`, `Improved`, `Improved-Maisonette`, `Maisonette`, `Model A`, `Model A-Maisonette`, `Model A2`, `Multi Generation`, `New Generation`, `Premium Apartment`, `Premium Apartment Loft`, `Premium Maisonette`, `Simplified`, `Standard`, `Terrace`, `Type S1`, `Type S2`

### 5. Unify `remaining_lease` → `remaining_lease_years`

Three formats in the raw data, all converted to a single integer column (unit: years, months ignored):

| Source | Format | Example | Conversion |
|--------|--------|---------|------------|
| 1990-2014 files | NaN (column missing) | — | `99 - transaction_year + lease_commence_date` |
| 2015-2016 file | Integer (years) | `70` | Keep as-is |
| 2017+ file | String | `"61 years 04 months"` | Take years only, ignore months → `61` |

Result range: 39 ~ 99 years. 51 rows with `remaining_lease_years > 99` were removed as data errors (`lease_commence_date` later than transaction year, which is impossible for a 99-year lease).

### 6. Validate `month`

Format `YYYY-MM` (string). All values parse successfully. Range: `1990-01` to `2026-09`.

### 7. Split `storey_range` → `storey_min`, `storey_max`

Original format: `"01 TO 03"`. Split on `" TO "` into two integer columns.

- `storey_min`: 1 ~ 49
- `storey_max`: 3 ~ 51

### 8. Numeric types

| Column | Type | Notes |
|--------|------|-------|
| `floor_area_sqm` | float | Has decimal values (e.g. 366.7) |
| `resale_price` | int | Range: 5,000 ~ 1,728,000 |
| `lease_commence_date` | int | Range: 1966 ~ 2023 |

### 9. String columns

`town`, `block`, `street_name` — already clean (uppercase), no changes needed. `block` ensured as string type (some are alphanumeric like `1A`).

## Output

`data/processed_data/resale_cleaned.csv` — **986,720 rows**, 12 columns, 0 nulls.

`data/processed_data/resale_removed.csv` — **51 rows** removed (with `removal_reason` column).

| Column | Type | Description |
|--------|------|-------------|
| `month` | str | Transaction month (YYYY-MM) |
| `town` | str | Town name |
| `flat_type` | str | Flat type (7 values) |
| `block` | str | Block number |
| `street_name` | str | Street name |
| `storey_min` | int | Storey range lower bound |
| `storey_max` | int | Storey range upper bound |
| `floor_area_sqm` | float | Floor area in sqm |
| `flat_model` | str | Flat model (21 values) |
| `lease_commence_date` | int | Lease commencement year |
| `remaining_lease_years` | int | Remaining lease in years |
| `resale_price` | int | Resale price in SGD |

## How to Run

```bash
python data/src/resale_clean.py
```

## Foreign Key (flat_id)

Not included in this stage. The `flat_id` foreign key references the HDB table (maintained by another team). It will be resolved during the Load phase by joining on `block = blk_no AND street_name = street`. The HDB table has been verified to have unique `(blk_no, street)` pairs (12,442 rows, 0 duplicates).
