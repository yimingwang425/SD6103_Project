-- Q4: Hawker Centre Proximity Effect
-- Price differential between NEAR (proximity_class=1, <=500m)
-- and FAR (proximity_class=3, >1000m) hawker proximity classes.
-- Uses the closest hawker per block (MIN proximity_class).

WITH block_class AS (
    SELECT block_id,
           MIN(proximity_class) AS proximity_class
    FROM   hawker_proximity
    GROUP  BY block_id
),
near_prices AS (
    SELECT t.town_name,
           f.flat_type,
           AVG(rt.resale_price) AS avg_price_near
    FROM   resale_transaction rt
    JOIN   hdb_flat f   ON rt.flat_id  = f.flat_id
    JOIN   hdb_block b  ON f.block_id  = b.block_id
    JOIN   town t       ON b.town_id   = t.town_id
    JOIN   block_class c ON b.block_id = c.block_id
    WHERE  c.proximity_class = 1
    GROUP  BY t.town_name, f.flat_type
),
far_prices AS (
    SELECT t.town_name,
           f.flat_type,
           AVG(rt.resale_price) AS avg_price_far
    FROM   resale_transaction rt
    JOIN   hdb_flat f   ON rt.flat_id  = f.flat_id
    JOIN   hdb_block b  ON f.block_id  = b.block_id
    JOIN   town t       ON b.town_id   = t.town_id
    JOIN   block_class c ON b.block_id = c.block_id
    WHERE  c.proximity_class = 3
    GROUP  BY t.town_name, f.flat_type
)
SELECT np.town_name,
       np.flat_type,
       ROUND(np.avg_price_near, 2)  AS avg_price_near_hawker,
       ROUND(fp.avg_price_far, 2)   AS avg_price_far_hawker,
       ROUND((np.avg_price_near - fp.avg_price_far) * 100.0
             / fp.avg_price_far, 2) AS price_diff_percentage
FROM   near_prices np
JOIN   far_prices fp ON np.town_name = fp.town_name
                    AND np.flat_type  = fp.flat_type
ORDER  BY np.town_name, np.flat_type;
