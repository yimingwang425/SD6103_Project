-- Q1: Price-per-Square-Meter Across Towns
-- Average price per square meter for 4-Room and 5-Room flats
-- sold within a 3-year range.
--
-- Five year ranges matching the incremental loading stages:
--   Stage 1 (after File 1: 1990-1999):  1991-1993
--   Stage 2 (after File 2: +2000-2012): 1998-2000
--   Stage 3 (after File 3: +2012-2014): 2005-2007
--   Stage 4 (after File 4: +2015-2016): 2013-2015
--   Stage 5 (after File 5: +2017-now):  2020-2022

-- ====== Stage 1: 1991-1993 (after loading 1990-1999) ======
SELECT t.town_name,
       f.flat_type,
       ROUND(AVG(rt.resale_price / f.floor_area_sqm), 2) AS avg_price_per_sqm
FROM   resale_transaction rt
JOIN   hdb_flat f  ON rt.flat_id  = f.flat_id
JOIN   hdb_block b ON f.block_id  = b.block_id
JOIN   town t      ON b.town_id   = t.town_id
WHERE  f.flat_type IN ('4 ROOM', '5 ROOM')
  AND  rt.transaction_year BETWEEN 1991 AND 1993
GROUP  BY t.town_name, f.flat_type
ORDER  BY t.town_name, f.flat_type;

-- ====== Stage 2: 1998-2000 (after loading +2000-2012) ======
SELECT t.town_name,
       f.flat_type,
       ROUND(AVG(rt.resale_price / f.floor_area_sqm), 2) AS avg_price_per_sqm
FROM   resale_transaction rt
JOIN   hdb_flat f  ON rt.flat_id  = f.flat_id
JOIN   hdb_block b ON f.block_id  = b.block_id
JOIN   town t      ON b.town_id   = t.town_id
WHERE  f.flat_type IN ('4 ROOM', '5 ROOM')
  AND  rt.transaction_year BETWEEN 1998 AND 2000
GROUP  BY t.town_name, f.flat_type
ORDER  BY t.town_name, f.flat_type;

-- ====== Stage 3: 2005-2007 (after loading +2012-2014) ======
SELECT t.town_name,
       f.flat_type,
       ROUND(AVG(rt.resale_price / f.floor_area_sqm), 2) AS avg_price_per_sqm
FROM   resale_transaction rt
JOIN   hdb_flat f  ON rt.flat_id  = f.flat_id
JOIN   hdb_block b ON f.block_id  = b.block_id
JOIN   town t      ON b.town_id   = t.town_id
WHERE  f.flat_type IN ('4 ROOM', '5 ROOM')
  AND  rt.transaction_year BETWEEN 2005 AND 2007
GROUP  BY t.town_name, f.flat_type
ORDER  BY t.town_name, f.flat_type;

-- ====== Stage 4: 2013-2015 (after loading +2015-2016) ======
SELECT t.town_name,
       f.flat_type,
       ROUND(AVG(rt.resale_price / f.floor_area_sqm), 2) AS avg_price_per_sqm
FROM   resale_transaction rt
JOIN   hdb_flat f  ON rt.flat_id  = f.flat_id
JOIN   hdb_block b ON f.block_id  = b.block_id
JOIN   town t      ON b.town_id   = t.town_id
WHERE  f.flat_type IN ('4 ROOM', '5 ROOM')
  AND  rt.transaction_year BETWEEN 2013 AND 2015
GROUP  BY t.town_name, f.flat_type
ORDER  BY t.town_name, f.flat_type;

-- ====== Stage 5: 2020-2022 (after loading +2017-now) ======
SELECT t.town_name,
       f.flat_type,
       ROUND(AVG(rt.resale_price / f.floor_area_sqm), 2) AS avg_price_per_sqm
FROM   resale_transaction rt
JOIN   hdb_flat f  ON rt.flat_id  = f.flat_id
JOIN   hdb_block b ON f.block_id  = b.block_id
JOIN   town t      ON b.town_id   = t.town_id
WHERE  f.flat_type IN ('4 ROOM', '5 ROOM')
  AND  rt.transaction_year BETWEEN 2020 AND 2022
GROUP  BY t.town_name, f.flat_type
ORDER  BY t.town_name, f.flat_type;
