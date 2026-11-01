-- Q5: Town Level Price Near Fresh Market Hawkers
-- Average resale price per proximity level for flats near hawkers
-- with fresh market stalls. Filter towns where avg price > 400000 SGD.
-- level_id: 1=NEAR (<=500m), 2=MID (500-1000m), 3=FAR (>1000m)

SELECT t.town_name,
       pl.category AS proximity_category,
       ROUND(AVG(rt.resale_price), 2) AS avg_resale_price
FROM   resale_transaction rt
JOIN   hdb_flat f         ON rt.flat_id   = f.flat_id
JOIN   hdb_block b        ON f.block_id   = b.block_id
JOIN   town t             ON b.town_id    = t.town_id
JOIN   proximity p        ON b.block_id   = p.block_id
JOIN   hawker_centre hc   ON p.hawker_id  = hc.hawker_id
JOIN   proximity_level pl ON p.level_id   = pl.level_id
WHERE  hc.no_of_mkt_produce_stalls > 0
GROUP  BY t.town_name, pl.category, pl.level_id
HAVING AVG(rt.resale_price) > 400000
ORDER  BY t.town_name, pl.level_id;
