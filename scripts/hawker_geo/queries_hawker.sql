WITH block_level AS (
    SELECT block_id,
           MIN(level_id) AS level_id
    FROM   proximity
    GROUP  BY block_id
),
priced AS (
    SELECT tn.town_name,
           bl.level_id,
           t.resale_price
    FROM   resale_transaction t
    JOIN   hdb_flat f      ON f.flat_id  = t.flat_id
    JOIN   hdb_block b     ON b.block_id = f.block_id
    JOIN   town tn         ON tn.town_id = b.town_id
    JOIN   block_level bl  ON bl.block_id = f.block_id
    WHERE  f.flat_type = '4 ROOM'
      AND  (t.transaction_year, t.transaction_month) >= (2020, 1)
)
SELECT pr.town_name,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pr.resale_price)
             FILTER (WHERE pr.level_id = 1)) AS median_level1,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pr.resale_price)
             FILTER (WHERE pr.level_id = 2)) AS median_level2,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pr.resale_price)
             FILTER (WHERE pr.level_id = 3)) AS median_level3,
       COUNT(*) FILTER (WHERE pr.level_id = 1) AS n1,
       COUNT(*) FILTER (WHERE pr.level_id = 2) AS n2,
       COUNT(*) FILTER (WHERE pr.level_id = 3) AS n3
FROM   priced pr
GROUP  BY pr.town_name
HAVING COUNT(*) FILTER (WHERE pr.level_id = 1) >= 30
   AND COUNT(*) FILTER (WHERE pr.level_id = 2) >= 30
   AND COUNT(*) FILTER (WHERE pr.level_id = 3) >= 30
ORDER  BY median_level1 DESC, pr.town_name;

WITH block_level AS (
    SELECT block_id,
           MIN(level_id) AS level_id
    FROM   proximity
    GROUP  BY block_id
),
priced AS (
    SELECT tn.town_name,
           bl.level_id,
           t.resale_price
    FROM   resale_transaction t
    JOIN   hdb_flat f      ON f.flat_id  = t.flat_id
    JOIN   hdb_block b     ON b.block_id = f.block_id
    JOIN   town tn         ON tn.town_id = b.town_id
    JOIN   block_level bl  ON bl.block_id = f.block_id
    WHERE  f.flat_type = '4 ROOM'
      AND  (t.transaction_year, t.transaction_month) >= (2020, 1)
),
counts AS (
    SELECT town_name,
           COUNT(*) FILTER (WHERE level_id = 1) AS n1,
           COUNT(*) FILTER (WHERE level_id = 2) AS n2,
           COUNT(*) FILTER (WHERE level_id = 3) AS n3
    FROM   priced
    GROUP  BY town_name
)
SELECT c.town_name, c.n1, c.n2, c.n3,
       CASE
           WHEN c.n1 = 0 OR c.n2 = 0 OR c.n3 = 0 THEN 'structural: level empty'
           ELSE 'thin: level too small'
       END AS why_excluded
FROM   counts c
WHERE  LEAST(c.n1, c.n2, c.n3) < 30
ORDER  BY LEAST(c.n1, c.n2, c.n3), c.town_name;

WITH block_fresh AS (
    SELECT b.block_id,
           EXISTS (
               SELECT 1
               FROM   proximity p
               JOIN   hawker_centre    h ON h.hawker_id = p.hawker_id
               WHERE  p.block_id = b.block_id
                 AND  p.level_id <= 2
                 AND  h.no_of_mkt_produce_stalls > 0
           ) AS has_fresh_market_nearby
    FROM   hdb_block b
)
SELECT f.has_fresh_market_nearby,
       COUNT(*)                                                          AS n_txn,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY t.resale_price)) AS median_price,
       ROUND(AVG(t.resale_price))                                        AS mean_price
FROM   resale_transaction t
JOIN   hdb_flat fl   ON fl.flat_id  = t.flat_id
JOIN   block_fresh f ON f.block_id  = fl.block_id
WHERE  fl.flat_type = '4 ROOM'
  AND  (t.transaction_year, t.transaction_month) >= (2020, 1)
GROUP  BY f.has_fresh_market_nearby
ORDER  BY f.has_fresh_market_nearby DESC;

WITH block_fresh AS (
    SELECT b.block_id,
           EXISTS (
               SELECT 1
               FROM   proximity p
               JOIN   hawker_centre    h ON h.hawker_id = p.hawker_id
               WHERE  p.block_id = b.block_id
                 AND  p.level_id <= 2
                 AND  (h.no_of_mkt_produce_stalls > 0
                       OR h.no_of_mkt_produce_stalls IS NULL)
           ) AS has_fresh_market_nearby
    FROM   hdb_block b
)
SELECT f.has_fresh_market_nearby,
       COUNT(*)                                                          AS n_txn,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY t.resale_price)) AS median_price,
       ROUND(AVG(t.resale_price))                                        AS mean_price
FROM   resale_transaction t
JOIN   hdb_flat fl   ON fl.flat_id  = t.flat_id
JOIN   block_fresh f ON f.block_id  = fl.block_id
WHERE  fl.flat_type = '4 ROOM'
  AND  (t.transaction_year, t.transaction_month) >= (2020, 1)
GROUP  BY f.has_fresh_market_nearby
ORDER  BY f.has_fresh_market_nearby DESC;

WITH block_flags AS (
    SELECT b.block_id,
           EXISTS (
               SELECT 1 FROM proximity p
               JOIN   hawker_centre h ON h.hawker_id = p.hawker_id
               WHERE  p.block_id = b.block_id
                 AND  h.no_of_mkt_produce_stalls > 0
           ) AS has_known_market,
           EXISTS (
               SELECT 1 FROM proximity p
               JOIN   hawker_centre h ON h.hawker_id = p.hawker_id
               WHERE  p.block_id = b.block_id
                 AND  h.no_of_mkt_produce_stalls IS NULL
           ) AS has_unknown_centre,
           EXISTS (
               SELECT 1 FROM proximity p
               WHERE  p.block_id = b.block_id
           ) AS has_any_centre
    FROM   hdb_block b
)
SELECT COUNT(*)                                                       AS n_blocks,
       COUNT(*) FILTER (WHERE has_known_market)                       AS with_known_market,
       COUNT(*) FILTER (WHERE NOT has_known_market)                   AS without_known_market,
       COUNT(*) FILTER (WHERE NOT has_known_market
                          AND has_unknown_centre)                     AS only_unknown_centres,
       COUNT(*) FILTER (WHERE NOT has_any_centre)                     AS no_centre_at_all,
       ROUND(100.0 * COUNT(*) FILTER (WHERE NOT has_known_market)
             / COUNT(*), 1)                                           AS pct_without
FROM   block_flags;
