-- queries_hawker.sql - Q4 / Q5 plus two diagnostics. Each query carries
-- its verified result underneath; verify_queries.py recomputes them from the CSVs.

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

-- Verified result: 21 of 26 towns survive. The result is NOT monotone - in
-- most towns level 2 is cheaper than level 1, but level 3 often ticks back up:
-- "far from a hawker centre" is really "far from everything", a proxy for
-- estate maturity rather than a clean proximity effect.
--
--   town_name            median_level1  median_level2  median_level3     n1     n2     n3
--   BUKIT MERAH                888,000        768,000        835,000    876  1,506    277
--   TOA PAYOH                  860,000        840,000        580,000    621  1,062    360
--   QUEENSTOWN                 790,000        875,000        850,000    387  1,010    673
--   KALLANG/WHAMPOA            750,000        813,250        728,000    561  1,082    503
--   GEYLANG                    734,000        650,000        490,000    728    543    394
--   PASIR RIS                  719,888        569,000        535,000    105    325  1,643
--   BISHAN                     666,944        601,500        665,000     48     82  1,301
--   BUKIT BATOK                655,000        638,000        514,444    131    942  2,374
--   CLEMENTI                   600,000        588,000        785,000    281    693    537
--   SENGKANG                   570,000        568,000        530,000  1,176  3,189  2,934
--   HOUGANG                    555,000        501,500        558,000    263  1,046  2,956
--   TAMPINES                   540,000        650,000        566,594     55  1,290  4,126
--   ANG MO KIO                 538,000        553,000        476,000    761  1,260     52
--   CHOA CHU KANG              525,000        508,000        498,000     69    473  3,488
--   BEDOK                      515,000        495,000        560,000    501  1,466  1,371
--   YISHUN                     510,000        488,000        488,000    441  2,184  3,219
--   SEMBAWANG                  500,000        495,000        575,000     47    517  2,057
--   BUKIT PANJANG              491,000        500,000        485,000    497    934  1,510
--   JURONG WEST                490,500        495,000        488,000    548  1,817  1,732
--   WOODLANDS                  475,000        490,000        503,000    181  1,908  3,434
--   JURONG EAST                460,000        468,000        523,000     59    546    439
--
-- Fractional medians (813,250 / 719,888 / 514,444 / 566,594 / 601,500) are
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

-- Verified result (5 rows):
--   town_name        n1    n2    n3   why_excluded
--   BUKIT TIMAH      48    97     0   structural: level empty   (n3 = 0)
--   MARINE PARADE    83   172     0   structural: level empty   (n3 = 0)
--   PUNGGOL           0   782  6230   structural: level empty   (n1 = 0)
--   SERANGOON          0     0  1315   structural: level empty   (n1 = n2 = 0)
--   CENTRAL AREA    451    83    10   thin: level too small     (n3 = 10)

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
  AND  (t.transaction_year, t.transaction_month) >= (2020, 1)
GROUP  BY f.has_fresh_market_nearby
ORDER  BY f.has_fresh_market_nearby DESC;

-- Verified result - STRICT (the headline number):
--   has_fresh_market_nearby |  n_txn | median_price | mean_price
--   true                    | 16,669 |      618,000 |    668,198
--   false                   | 60,193 |      538,000 |    555,304
--   -> median gap +14.9%, mean gap +20.3%

-- Q5 sensitivity. Same query, but the 23 attribute-unknown centres are treated
-- as having a fresh market (the optimistic bound). The gap collapses from
-- +14.9% to +5.6%: two thirds of the measured premium depends on an assumption
-- about 23 centres we have no stall data for. Report both numbers - do not
-- quote +14.9% alone.
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
  AND  (t.transaction_year, t.transaction_month) >= (2020, 1)
GROUP  BY f.has_fresh_market_nearby
ORDER  BY f.has_fresh_market_nearby DESC;

-- Verified result - LENIENT:
--   has_fresh_market_nearby |  n_txn | median_price | mean_price
--   true                    | 33,863 |      565,000 |    615,233
--   false                   | 42,999 |      535,000 |    551,872
--   -> median gap +5.6%, mean gap +11.5%

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
--      10194 |              6798 |                 3396 |                3316 |               80 |        33.3
