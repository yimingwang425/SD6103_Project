-- Q1: Price-per-Square-Meter Across Towns
-- Average price per square meter for 4-Room and 5-Room flats
-- sold within a 3-year range.
--
-- Five year ranges matching the incremental loading stages:
--   Stage 1 (after File 1: 1990-1999):  1991-1993
--   Stage 2 (after File 2: +2000-2012): 1998-2000
--   Stage 3 (after File 3: +2012-2014): 2005-2007
--   Stage 4 (after File 4: +2015-2016): 2013-2015
--   Stage 5 (after File 5: +2017-now):  2020-2022

-- Stage 1: 1991-1993 (after loading 1990-1999)
SELECT t.town_name,
       f.flat_type,
       ROUND(AVG(rt.resale_price / f.floor_area_sqm), 2) AS avg_price_per_sqm
FROM   resale_transaction rt
JOIN   hdb_flat f  ON rt.flat_id  = f.flat_id
JOIN   hdb_block b ON f.block_id  = b.block_id
JOIN   town t      ON b.town_id   = t.town_id
WHERE  f.flat_type IN ('4 ROOM', '5 ROOM')
  AND  rt.transaction_year BETWEEN 1991 AND 1993
GROUP  BY t.town_name, f.flat_type
ORDER  BY t.town_name, f.flat_type;

-- Stage 2: 1998-2000 (after loading +2000-2012)
SELECT t.town_name,
       f.flat_type,
       ROUND(AVG(rt.resale_price / f.floor_area_sqm), 2) AS avg_price_per_sqm
FROM   resale_transaction rt
JOIN   hdb_flat f  ON rt.flat_id  = f.flat_id
JOIN   hdb_block b ON f.block_id  = b.block_id
JOIN   town t      ON b.town_id   = t.town_id
WHERE  f.flat_type IN ('4 ROOM', '5 ROOM')
  AND  rt.transaction_year BETWEEN 1998 AND 2000
GROUP  BY t.town_name, f.flat_type
ORDER  BY t.town_name, f.flat_type;

-- Stage 3: 2005-2007 (after loading +2012-2014)
SELECT t.town_name,
       f.flat_type,
       ROUND(AVG(rt.resale_price / f.floor_area_sqm), 2) AS avg_price_per_sqm
FROM   resale_transaction rt
JOIN   hdb_flat f  ON rt.flat_id  = f.flat_id
JOIN   hdb_block b ON f.block_id  = b.block_id
JOIN   town t      ON b.town_id   = t.town_id
WHERE  f.flat_type IN ('4 ROOM', '5 ROOM')
  AND  rt.transaction_year BETWEEN 2005 AND 2007
GROUP  BY t.town_name, f.flat_type
ORDER  BY t.town_name, f.flat_type;

-- Stage 4: 2013-2015 (after loading +2015-2016)
SELECT t.town_name,
       f.flat_type,
       ROUND(AVG(rt.resale_price / f.floor_area_sqm), 2) AS avg_price_per_sqm
FROM   resale_transaction rt
JOIN   hdb_flat f  ON rt.flat_id  = f.flat_id
JOIN   hdb_block b ON f.block_id  = b.block_id
JOIN   town t      ON b.town_id   = t.town_id
WHERE  f.flat_type IN ('4 ROOM', '5 ROOM')
  AND  rt.transaction_year BETWEEN 2013 AND 2015
GROUP  BY t.town_name, f.flat_type
ORDER  BY t.town_name, f.flat_type;

-- Stage 5: 2020-2022 (after loading +2017-now)
SELECT t.town_name,
       f.flat_type,
       ROUND(AVG(rt.resale_price / f.floor_area_sqm), 2) AS avg_price_per_sqm
FROM   resale_transaction rt
JOIN   hdb_flat f  ON rt.flat_id  = f.flat_id
JOIN   hdb_block b ON f.block_id  = b.block_id
JOIN   town t      ON b.town_id   = t.town_id
WHERE  f.flat_type IN ('4 ROOM', '5 ROOM')
  AND  rt.transaction_year BETWEEN 2020 AND 2022
GROUP  BY t.town_name, f.flat_type
ORDER  BY t.town_name, f.flat_type;


-- Q2: Remaining Lease vs. Transaction Price
-- Categorize transactions into lease buckets, compute median price,
-- average price per sqm, and transaction volume per bucket per town.

SELECT t.town_name,
       CASE
           WHEN rt.remaining_lease_years < 50          THEN 'Under 50'
           WHEN rt.remaining_lease_years BETWEEN 50 AND 70 THEN '50-70'
           WHEN rt.remaining_lease_years BETWEEN 71 AND 90 THEN '71-90'
           ELSE 'Above 90'
       END AS lease_bucket,
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY rt.resale_price)
           AS median_resale_price,
       ROUND(AVG(rt.resale_price / f.floor_area_sqm), 2)
           AS avg_price_per_sqm,
       COUNT(*) AS transaction_volume
FROM   resale_transaction rt
JOIN   hdb_flat f  ON rt.flat_id  = f.flat_id
JOIN   hdb_block b ON f.block_id  = b.block_id
JOIN   town t      ON b.town_id   = t.town_id
GROUP  BY t.town_name, lease_bucket
ORDER  BY t.town_name, lease_bucket;


-- Q3: Resale Price for Towns with Large Flats
-- Find top 10 towns by average floor area for 3-Room flats,
-- then retrieve the highest resale price per year for 3-Room flats
-- in those towns.

WITH top_towns AS (
    SELECT   b.town_id,
             t.town_name,
             AVG(f.floor_area_sqm) AS avg_floor_area
    FROM     hdb_flat f
    JOIN     hdb_block b ON f.block_id = b.block_id
    JOIN     town t      ON b.town_id  = t.town_id
    WHERE    f.flat_type = '3 ROOM'
    GROUP BY b.town_id, t.town_name
    ORDER BY avg_floor_area DESC
    LIMIT    10
)
SELECT tt.town_name,
       ROUND(tt.avg_floor_area, 1) AS avg_floor_area,
       rt.transaction_year,
       MAX(rt.resale_price) AS max_resale_price
FROM   top_towns tt
JOIN   hdb_block b ON tt.town_id  = b.town_id
JOIN   hdb_flat f  ON b.block_id  = f.block_id
JOIN   resale_transaction rt ON f.flat_id = rt.flat_id
WHERE  f.flat_type = '3 ROOM'
GROUP  BY tt.town_name, tt.avg_floor_area, rt.transaction_year
ORDER  BY tt.town_name, rt.transaction_year;


-- Q4: Hawker Centre Proximity Effect
-- Price differential between NEAR (level_id=1, <=500m)
-- and FAR (level_id=3, >1000m) hawker proximity levels.
-- Uses the closest hawker per block (MIN level_id).

WITH block_level AS (
    SELECT block_id,
           MIN(level_id) AS level_id
    FROM   proximity
    GROUP  BY block_id
),
near_prices AS (
    SELECT t.town_name,
           f.flat_type,
           AVG(rt.resale_price) AS avg_price_near
    FROM   resale_transaction rt
    JOIN   hdb_flat f    ON rt.flat_id  = f.flat_id
    JOIN   hdb_block b   ON f.block_id  = b.block_id
    JOIN   town t        ON b.town_id   = t.town_id
    JOIN   block_level c ON b.block_id  = c.block_id
    WHERE  c.level_id = 1
    GROUP  BY t.town_name, f.flat_type
),
far_prices AS (
    SELECT t.town_name,
           f.flat_type,
           AVG(rt.resale_price) AS avg_price_far
    FROM   resale_transaction rt
    JOIN   hdb_flat f    ON rt.flat_id  = f.flat_id
    JOIN   hdb_block b   ON f.block_id  = b.block_id
    JOIN   town t        ON b.town_id   = t.town_id
    JOIN   block_level c ON b.block_id  = c.block_id
    WHERE  c.level_id = 3
    GROUP  BY t.town_name, f.flat_type
)
SELECT np.town_name,
       np.flat_type,
       ROUND(np.avg_price_near, 2)  AS avg_price_near_hawker,
       ROUND(fp.avg_price_far, 2)   AS avg_price_far_hawker,
       ROUND((np.avg_price_near - fp.avg_price_far) * 100.0
             / fp.avg_price_far, 2) AS price_diff_percentage
FROM   near_prices np
JOIN   far_prices fp ON np.town_name = fp.town_name
                    AND np.flat_type  = fp.flat_type
ORDER  BY np.town_name, np.flat_type;


-- Q5: Town Level Price Near Fresh Market Hawkers
-- Average resale price per proximity level for flats near hawkers
-- with fresh market stalls. Keep only town-and-level groups whose
-- average price exceeds 400000 SGD.
-- level_id: 1=NEAR (<=500m), 2=MID (500-1000m), 3=FAR (>1000m)
-- Each block takes the level of its closest fresh-market centre, so the
-- levels do not overlap and each transaction is counted once.
-- Totalling each block's sales first keeps the join to one row per block.

WITH closest_fresh_level AS (
    SELECT p.block_id,
           MIN(p.level_id) AS level_id
    FROM   proximity p
    JOIN   hawker_centre hc ON p.hawker_id = hc.hawker_id
    WHERE  hc.no_of_mkt_produce_stalls > 0
    GROUP  BY p.block_id
),
block_sales AS (
    SELECT f.block_id,
           SUM(rt.resale_price) AS total_price,
           COUNT(*)             AS sales
    FROM   resale_transaction rt
    JOIN   hdb_flat f ON rt.flat_id = f.flat_id
    GROUP  BY f.block_id
)
SELECT t.town_name,
       pl.category AS proximity_category,
       ROUND(SUM(s.total_price) / SUM(s.sales), 2) AS avg_resale_price
FROM   block_sales s
JOIN   hdb_block b             ON s.block_id = b.block_id
JOIN   town t                  ON b.town_id  = t.town_id
JOIN   closest_fresh_level c   ON b.block_id = c.block_id
JOIN   proximity_level pl      ON c.level_id = pl.level_id
GROUP  BY t.town_name, pl.category, pl.level_id
HAVING SUM(s.total_price) / SUM(s.sales) > 400000
ORDER  BY t.town_name, pl.level_id;


-- Q6: Town Hawker Density vs Resale Price Overview (Custom Query)
-- For each town, compare the average resale price with the number of
-- nearby hawker centres (NEAR level, <=500m) and how many of those
-- have fresh market stalls.
-- Prices and hawker counts are aggregated separately, so a block with
-- several NEAR centres does not count its transactions more than once.
-- Joins ALL 7 tables: town, hdb_block, hdb_flat, resale_transaction,
-- proximity, proximity_level, hawker_centre.

WITH near_pairs AS (
    SELECT p.block_id,
           hc.hawker_id,
           hc.no_of_mkt_produce_stalls
    FROM   proximity p
    JOIN   proximity_level pl ON p.level_id  = pl.level_id
    JOIN   hawker_centre hc   ON p.hawker_id = hc.hawker_id
    WHERE  pl.category = 'NEAR'
),
sold_near_blocks AS (
    SELECT DISTINCT np.block_id
    FROM   near_pairs np
    JOIN   hdb_flat f ON np.block_id = f.block_id
),
town_prices AS (
    SELECT b.town_id,
           COUNT(*) AS total_transactions,
           ROUND(AVG(rt.resale_price), 0) AS avg_resale_price
    FROM   resale_transaction rt
    JOIN   hdb_flat f          ON rt.flat_id = f.flat_id
    JOIN   hdb_block b         ON f.block_id = b.block_id
    JOIN   sold_near_blocks sn ON b.block_id = sn.block_id
    GROUP  BY b.town_id
),
town_hawkers AS (
    SELECT b.town_id,
           COUNT(DISTINCT np.hawker_id) AS nearby_hawker_count,
           COUNT(DISTINCT np.hawker_id)
               FILTER (WHERE np.no_of_mkt_produce_stalls > 0) AS fresh_market_count
    FROM   near_pairs np
    JOIN   sold_near_blocks sn ON np.block_id = sn.block_id
    JOIN   hdb_block b         ON np.block_id = b.block_id
    GROUP  BY b.town_id
)
SELECT t.town_name,
       tp.total_transactions,
       tp.avg_resale_price,
       th.nearby_hawker_count,
       th.fresh_market_count
FROM   town t
JOIN   town_prices tp  ON t.town_id = tp.town_id
JOIN   town_hawkers th ON t.town_id = th.town_id
ORDER  BY tp.avg_resale_price DESC, t.town_name;


