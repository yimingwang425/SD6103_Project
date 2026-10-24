# Task 2: Database Population Report

## 1. Database Overview

Database: `sd6103_project` (PostgreSQL)

### Tables and Row Counts

| Table | Rows | Description |
|-------|------|-------------|
| town | 26 | Town dimension table |
| hdb_block | 10,194 | HDB block with geolocation |
| hdb_flat | 145,465 | Unique flat types within blocks |
| resale_transaction | 973,782 | Resale transaction records |
| hawker_centre | 129 | Hawker centre with geolocation |
| proximity_level | 3 | Proximity class definitions |
| proximity | 10,194 | Block-to-nearest-hawker distance |

## 2. Schema Design and Constraints

### Primary Keys

| Table | Primary Key |
|-------|-------------|
| town | town_id (SERIAL) |
| hdb_block | block_id (SERIAL) |
| hdb_flat | flat_id (SERIAL) |
| resale_transaction | transaction_id (SERIAL) |
| hawker_centre | hawker_id (SERIAL) |
| proximity_level | level_id (INTEGER) |
| proximity | (block_id, hawker_id) composite |

### Foreign Keys

The foreign key chain follows the ER model:

```
town.town_id
  └── hdb_block.town_id          -- each block belongs to one town
        ├── hdb_flat.block_id    -- each flat belongs to one block
        │     └── resale_transaction.flat_id  -- each transaction belongs to one flat
        └── proximity.block_id   -- each proximity record links a block...
              ├── hawker_centre.hawker_id     -- ...to a hawker centre
              └── proximity_level.level_id    -- ...with a proximity class
```

Specifically:

| Child Table | Column | References | Parent Column |
|-------------|--------|------------|---------------|
| hdb_block | town_id | town | town_id |
| hdb_flat | block_id | hdb_block | block_id |
| resale_transaction | flat_id | hdb_flat | flat_id |
| proximity | block_id | hdb_block | block_id |
| proximity | hawker_id | hawker_centre | hawker_id |
| proximity | level_id | proximity_level | level_id |

### CHECK Constraints

| Table | Column | Constraint |
|-------|--------|------------|
| resale_transaction | transaction_month | BETWEEN 1 AND 12 |
| resale_transaction | remaining_lease_years | BETWEEN 0 AND 99 |
| resale_transaction | resale_price | > 0 |

### UNIQUE Constraints

| Table | Columns | Purpose |
|-------|---------|---------|
| town | town_name | No duplicate town names |
| hdb_block | (blk_no, street) | No duplicate blocks |

## 3. Data Transformations

### 3.1 Resale Data (RESALE_CLEANED.csv → hdb_flat + resale_transaction)

The raw resale data is a single flat file with 986,720 rows. Each row represents one transaction and contains both flat attributes and transaction attributes. To match the ER model, the data was split into two tables:

**HDB Flat table generation:**
- A flat is defined as a unique combination of `(block_id, flat_type, storey_min, storey_max, floor_area_sqm, flat_model)`.
- This combination represents a flat *type* within a storey band in a block, not a single physical unit. The raw data does not contain unit-level identifiers (e.g., exact floor or unit number), so this is the finest granularity available.
- Each unique combination was assigned an auto-incrementing `flat_id`.
- Result: 145,465 unique flat types extracted.

**Resale Transaction table generation:**
- Each row from the cleaned resale CSV becomes one transaction record.
- The `month` field (format `YYYY-MM`) was split into `transaction_year` (INTEGER) and `transaction_month` (INTEGER).
- Each transaction was linked to its corresponding `flat_id` via the flat combination described above.

**Block ID matching:**
- The resale data identifies blocks by `(block, street_name)`. These were matched to `block_id` from the `hdb_block` table using an exact match on `(blk_no, street)` after uppercasing both sides.
- 9,837 out of 10,009 unique resale block keys matched (98.3%).
- 172 block keys (involving 12,938 transactions, 1.31%) had no match and were excluded. These are older blocks not present in the HDB geolocation dataset (e.g., blocks on EAST COAST RD, JLN PASAR BARU, REDHILL CL).

### 3.2 HDB Block Data (hdb_block_clean_YM.csv → hdb_block)

- The original CSV contains 37 columns. Only 8 columns required by the schema were extracted: `block_id, town_id, blk_no, street, year_completed, lat, lng, postal`.
- A separate script (`prepare_hdb_block.py`) produced the trimmed CSV for import.

### 3.3 Hawker Centre Data (hawker_centres_clean.csv → hawker_centre)

- The raw CSV includes a `source_row` column not needed in the database. A temporary table was used during import to skip this column.
- `has_fresh_market` was converted from INTEGER (0/1) to BOOLEAN.

### 3.4 Proximity Data (block_hawker_proximity.csv → proximity)

- The raw proximity CSV uses `(blk_no, street)` to identify blocks. A Python script (`prepare_proximity.py`) mapped these to `block_id` using the same lookup table as the resale data.
- 10,194 out of 12,442 proximity rows matched. The remaining 2,248 rows are blocks present in the geolocation dataset but not in the cleaned HDB block table (due to deduplication or filtering in the block cleaning step).

### 3.5 Proximity Level (static data)

Three proximity classes were defined based on haversine distance:

| level_id | Category | Distance Range |
|----------|----------|----------------|
| 1 | NEAR | 0 – 800 m |
| 2 | MEDIUM | 800.1 – 2,000 m |
| 3 | FAR | > 2,000 m |

## 4. Data Loading

All data was loaded using PostgreSQL's `\copy` command for bulk import. The loading order respects foreign key dependencies:

1. `town` → `hdb_block` (hdb_tables.sql)
2. `hawker_centre` → `proximity_level` → `proximity` (geo_tables.sql)
3. `hdb_flat` → `resale_transaction` (create_and_load.sql)

After loading, SERIAL sequences were reset to the maximum existing ID in each table using `setval()`.

## 5. Errors and Issues Encountered During Loading

| Issue | Cause | Resolution |
|-------|-------|------------|
| 1.31% resale transactions skipped | 172 blocks in resale data not found in HDB geolocation dataset | Excluded — these are older/demolished blocks without coordinates |
| Town `LIM CHU KANG` missing from town table | No HDB blocks in geolocation data for this town | Transactions for this town are among the skipped 1.31% |
| hdb_block CSV has 37 columns | Block cleaning script included extra demographic columns | Created `prepare_hdb_block.py` to extract only the 8 needed columns |
| hawker_centres_clean.csv has extra `source_row` column | Cleaning script retained it for traceability | Used a temp table during import to skip the column |
| 2,248 proximity rows skipped | Block keys in proximity data not matching cleaned block table | Excluded — minor deduplication differences between datasets |

## 6. Scripts

| Script | Purpose |
|--------|---------|
| `data/src/resale/RESALE_CLEAN.py` | Clean raw resale CSV (standardise types, parse lease, split storey range) |
| `data/src/resale/split_resale.py` | Split cleaned resale CSV into hdb_flat.csv + resale_transaction.csv |
| `data/src/prepare_hdb_block.py` | Extract needed columns from hdb_block_clean_YM.csv |
| `data/src/resale/prepare_proximity.py` | Map proximity (blk_no, street) to block_id |
| `scripts/hawker_geo_pipeline.py` | Clean hawker data and compute block-hawker distances |
| `data/src/hdb_tables.sql` | Create and load town + hdb_block tables |
| `data/src/geo_tables.sql` | Create and load hawker_centre + proximity_level + proximity tables |
| `data/src/resale/create_and_load.sql` | Create and load hdb_flat + resale_transaction tables |
