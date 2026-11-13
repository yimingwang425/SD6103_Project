-- Q3: Resale Price for Towns with Large Flats
-- Find top 10 towns by average floor area for 3-Room flats,
-- then retrieve the highest resale price per year for 3-Room flats
-- in those towns.

WITH top_towns AS (
    SELECT   b.town_id,
             t.town_name,
             AVG(f.floor_area_sqm) AS avg_floor_area
    FROM     hdb_flat f
    JOIN     hdb_block b ON f.block_id = b.block_id
    JOIN     town t      ON b.town_id  = t.town_id
    WHERE    f.flat_type = '3 ROOM'
    GROUP BY b.town_id, t.town_name
    ORDER BY avg_floor_area DESC
    LIMIT    10
)
SELECT tt.town_name,
       ROUND(tt.avg_floor_area, 1) AS avg_floor_area,
       rt.transaction_year,
       MAX(rt.resale_price) AS max_resale_price
FROM   top_towns tt
JOIN   hdb_block b ON tt.town_id  = b.town_id
JOIN   hdb_flat f  ON b.block_id  = f.block_id
JOIN   resale_transaction rt ON f.flat_id = rt.flat_id
WHERE  f.flat_type = '3 ROOM'
GROUP  BY tt.town_name, tt.avg_floor_area, rt.transaction_year
ORDER  BY tt.town_name, rt.transaction_year;
