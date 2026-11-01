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
