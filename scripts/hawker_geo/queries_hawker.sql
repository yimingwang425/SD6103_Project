-- queries_hawker.sql - Q4 / Q5 plus the two diagnostics that keep them honest.
-- Prerequisite: schema_hawker.sql has been loaded.
-- Each query carries its verified result underneath, so a reviewer can tell
-- whether their own run matches. verify_queries.py re-computes the same numbers
-- from the committed CSVs without a database.

-- Q4. Does walking distance to a hawker centre associate with resale price,
-- once town and flat type are held fixed? 4 ROOM and 2020-01 onward only,
-- compared WITHIN town, HAVING n >= 30 per level so a median means something.
WITH priced AS (
    SELECT tn.town_name,
           pl.level_id,
           t.resale_price
    FROM   resale_transaction t
    JOIN   hdb_flat f                   ON f.flat_id  = t.flat_id
    JOIN   hdb_block b                  ON b.block_id = f.block_id
    JOIN   town tn                      ON tn.town_id = b.town_id
    JOIN   hdb_block_proximity_level pl ON pl.block_id = f.block_id
    WHERE  f.flat_type = '4 ROOM'
      AND  t.transaction_month >= DATE '2020-01-01'
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
-- tiebreaker is not cosmetic: BUKIT PANJANG and JURONG WEST both land on a
-- level-1 median of exactly S$490,000
ORDER  BY median_level1 DESC, pr.town_name;

-- Verified result: 21 of 26 towns survive. The result is NOT monotone - in
-- most towns level 2 is cheaper than level 1, but level 3 often ticks back up:
-- "far from a hawker centre" is really "far from everything", a proxy for
-- estate maturity rather than a clean proximity effect.
--
--   town_name            median_level1  median_level2  median_level3     n1     n2     n3
--   BUKIT MERAH                880,000        765,000        833,000    862  1,475    271
--   TOA PAYOH                  860,000        822,500        580,000    616  1,018    356
--   QUEENSTOWN                 790,000        870,000        850,000    373    986    663
--   KALLANG/WHAMPOA            748,388        813,250        730,000    549  1,062    494
--   GEYLANG                    735,000        648,000        490,000    713    535    390
--   PASIR RIS                  719,888        568,444        535,000    105    322  1,626
--   BISHAN                     660,000        601,500        660,000     45     80  1,277
--   BUKIT BATOK                655,000        636,500        515,000    129    920  2,338
--   CLEMENTI                   600,000        588,000        783,000    279    683    527
--   SENGKANG                   570,000        565,000        527,500  1,161  3,158  2,892
--   HOUGANG                    555,000        500,000        555,000    260  1,037  2,909
--   TAMPINES                   540,000        645,000        565,000     55  1,256  4,044
--   ANG MO KIO                 535,000        550,000        480,000    751  1,242     49
--   CHOA CHU KANG              525,000        505,000        495,944     69    468  3,442
--   BEDOK                      515,000        492,000        560,000    495  1,437  1,334
--   YISHUN                     508,888        488,000        485,000    435  2,164  3,171
--   SEMBAWANG                  500,000        490,000        575,000     47    507  2,025
--   BUKIT PANJANG              490,000        500,000        485,000    491    922  1,493
--   JURONG WEST                490,000        495,000        488,000    539  1,786  1,712
--   WOODLANDS                  470,000        490,000        500,000    175  1,874  3,386
--   JURONG EAST                456,500        469,000        521,052     58    540    428
--
-- Fractional medians (748,388 / 719,888 / 495,944 / 508,888 / 521,052) are
-- correct: those towns have an even number of transactions in that level, so
-- PERCENTILE_CONT interpolates between the two middle prices.

-- Q4 diagnostic. Which towns dropped out, and was it because the level is
-- structurally empty or merely too thin to summarise? ("21 of 26" looks like
-- data loss; it is not.)
WITH priced AS (
    SELECT tn.town_name,
           pl.level_id,
           t.resale_price
    FROM   resale_transaction t
    JOIN   hdb_flat f                   ON f.flat_id  = t.flat_id
    JOIN   hdb_block b                  ON b.block_id = f.block_id
    JOIN   town tn                      ON tn.town_id = b.town_id
    JOIN   hdb_block_proximity_level pl ON pl.block_id = f.block_id
    WHERE  f.flat_type = '4 ROOM'
      AND  t.transaction_month >= DATE '2020-01-01'
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

-- Verified result (5 rows):
--   town_name        n1    n2    n3   why_excluded
--   BUKIT TIMAH      47    96     0   structural: level empty   (n3 = 0)
--   MARINE PARADE    82   169     0   structural: level empty   (n3 = 0)
--   PUNGGOL           0   770  6144   structural: level empty   (n1 = 0)
--   SERANGOON          0     0  1291   structural: level empty   (n1 = n2 = 0)
--   CENTRAL AREA    440    80    10   thin: level too small     (n3 = 10)

-- Q5. Does having a fresh market within a 1 km walk associate with price?
-- "Has a fresh market" = no_of_mkt_produce_stalls > 0 from the official stall
-- register, NOT the centre's name (only 20 of 107 named centres contain the
-- word "market"). The 23 centres with NULL stall counts are excluded here
-- (strict); the sensitivity variant below treats them as having one.
WITH block_fresh AS (
    SELECT b.block_id,
           EXISTS (
               SELECT 1
               FROM   proximity p
               JOIN   hawker_centre    h ON h.hawker_id = p.hawker_id
               WHERE  p.block_id = b.block_id
                 AND  p.distance_m <= 1000
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
  AND  t.transaction_month >= DATE '2020-01-01'
GROUP  BY f.has_fresh_market_nearby
ORDER  BY f.has_fresh_market_nearby DESC;

-- Verified result - STRICT (the headline number):
--   has_fresh_market_nearby |  n_txn | median_price | mean_price
--   true                    | 16,394 |      615,000 |    666,332
--   false                   | 59,241 |      536,000 |    553,663
--   -> median gap +14.7%, mean gap +20.3%

-- Q5 sensitivity. Same query, but the 23 attribute-unknown centres are treated
-- as having a fresh market (the optimistic bound). The gap collapses from
-- +14.7% to +5.0%: two thirds of the measured premium depends on an assumption
-- about 22 centres we have no stall data for. Report both numbers - do not
-- quote +14.7% alone.
WITH block_fresh AS (
    SELECT b.block_id,
           EXISTS (
               SELECT 1
               FROM   proximity p
               JOIN   hawker_centre    h ON h.hawker_id = p.hawker_id
               WHERE  p.block_id = b.block_id
                 AND  p.distance_m <= 1000
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
  AND  t.transaction_month >= DATE '2020-01-01'
GROUP  BY f.has_fresh_market_nearby
ORDER  BY f.has_fresh_market_nearby DESC;

-- Verified result - LENIENT:
--   has_fresh_market_nearby |  n_txn | median_price | mean_price
--   true                    | 33,304 |      562,000 |    613,182
--   false                   | 42,331 |      535,000 |    550,471
--   -> median gap +5.0%, mean gap +11.4%

-- Coverage diagnostic. How much of the housing stock can this analysis even
-- speak about? This number should accompany Q5 wherever it is quoted.
WITH block_flags AS (
    SELECT b.block_id,
           EXISTS (
               SELECT 1 FROM proximity p
               JOIN   hawker_centre h ON h.hawker_id = p.hawker_id
               WHERE  p.block_id = b.block_id
                 AND  p.distance_m <= 3000
                 AND  h.no_of_mkt_produce_stalls > 0
           ) AS has_known_market,
           EXISTS (
               SELECT 1 FROM proximity p
               JOIN   hawker_centre h ON h.hawker_id = p.hawker_id
               WHERE  p.block_id = b.block_id
                 AND  p.distance_m <= 3000
                 AND  h.no_of_mkt_produce_stalls IS NULL
           ) AS has_unknown_centre,
           EXISTS (
               SELECT 1 FROM proximity p
               WHERE  p.block_id = b.block_id
                 AND  p.distance_m <= 3000
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

-- Verified result:
--   n_blocks | with_known_market | without_known_market | only_unknown_centres | no_centre_at_all | pct_without
--      12442 |              8122 |                 4320 |                4217 |              103 |        34.7
