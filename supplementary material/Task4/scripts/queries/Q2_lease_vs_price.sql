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
