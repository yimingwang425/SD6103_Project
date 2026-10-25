-- Q6: Town Hawker Density vs Resale Price Overview (Custom Query)
-- For each town, compare the average resale price with the number of
-- nearby hawker centres (proximity_class=1, <=500m) and how many of
-- those have fresh market stalls.
-- Joins all 6 tables: town, hdb_block, hdb_flat, resale_transaction,
-- hawker_proximity, hawker_centre.

SELECT t.town_name,
       COUNT(DISTINCT rt.transaction_id) AS total_transactions,
       ROUND(AVG(rt.resale_price), 0) AS avg_resale_price,
       COUNT(DISTINCT hc.hawker_id) AS nearby_hawker_count,
       COUNT(DISTINCT CASE WHEN hc.no_of_mkt_produce_stalls > 0
             THEN hc.hawker_id END) AS fresh_market_count
FROM   town t
JOIN   hdb_block b         ON t.town_id    = b.town_id
JOIN   hdb_flat f          ON b.block_id   = f.block_id
JOIN   resale_transaction rt ON f.flat_id  = rt.flat_id
JOIN   hawker_proximity hp ON b.block_id   = hp.block_id
JOIN   hawker_centre hc   ON hp.hawker_id  = hc.hawker_id
WHERE  hp.proximity_class = 1
GROUP  BY t.town_name
ORDER  BY avg_resale_price DESC;
