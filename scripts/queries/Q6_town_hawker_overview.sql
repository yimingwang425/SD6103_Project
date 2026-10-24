-- Q6: Town-Level Resale Overview with Hawker Proximity (Custom Query)
-- For each town, show the number of transactions, average resale price,
-- average floor area, average remaining lease, and the number of
-- nearby hawker centres (proximity_class=1, <=500m) with fresh markets.
-- Joins 6 tables: town, hdb_block, hdb_flat, resale_transaction,
-- hawker_proximity, hawker_centre.

WITH town_resale AS (
    SELECT b.town_id,
           COUNT(rt.transaction_id) AS total_transactions,
           ROUND(AVG(rt.resale_price), 0) AS avg_resale_price,
           ROUND(AVG(f.floor_area_sqm), 1) AS avg_floor_area,
           ROUND(AVG(rt.remaining_lease_years), 1) AS avg_remaining_lease
    FROM   resale_transaction rt
    JOIN   hdb_flat f  ON rt.flat_id = f.flat_id
    JOIN   hdb_block b ON f.block_id = b.block_id
    GROUP  BY b.town_id
),
town_fresh_hawkers AS (
    SELECT b.town_id,
           COUNT(DISTINCT hc.hawker_id) AS fresh_hawker_count
    FROM   hdb_block b
    JOIN   hawker_proximity hp ON b.block_id  = hp.block_id
    JOIN   hawker_centre hc   ON hp.hawker_id = hc.hawker_id
    WHERE  hp.proximity_class = 1
      AND  hc.no_of_mkt_produce_stalls > 0
    GROUP  BY b.town_id
)
SELECT t.town_name,
       tr.total_transactions,
       tr.avg_resale_price,
       tr.avg_floor_area,
       tr.avg_remaining_lease,
       COALESCE(fh.fresh_hawker_count, 0) AS nearby_fresh_hawkers
FROM   town t
JOIN   town_resale tr           ON t.town_id = tr.town_id
LEFT JOIN town_fresh_hawkers fh ON t.town_id = fh.town_id
ORDER  BY tr.total_transactions DESC;
