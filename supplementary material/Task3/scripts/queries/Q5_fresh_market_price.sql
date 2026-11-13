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
