-- Q5: Town Level Price Near Fresh Market Hawkers
-- Average resale price per proximity class for flats near hawkers
-- with fresh market stalls. Filter towns where avg price > 400000 SGD.
-- proximity_class: 1=very near (<=500m), 2=near (500-1000m), 3=far (>1000m)

SELECT t.town_name,
       hp.proximity_class,
       ROUND(AVG(rt.resale_price), 2) AS avg_resale_price
FROM   resale_transaction rt
JOIN   hdb_flat f        ON rt.flat_id   = f.flat_id
JOIN   hdb_block b       ON f.block_id   = b.block_id
JOIN   town t            ON b.town_id    = t.town_id
JOIN   hawker_proximity hp ON b.block_id = hp.block_id
JOIN   hawker_centre hc  ON hp.hawker_id = hc.hawker_id
WHERE  hc.no_of_mkt_produce_stalls > 0
GROUP  BY t.town_name, hp.proximity_class
HAVING AVG(rt.resale_price) > 400000
ORDER  BY t.town_name, hp.proximity_class;
