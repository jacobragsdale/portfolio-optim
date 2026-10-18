-- Oracle. Bonds dealers are offering today.
SELECT o.cusip,
       CAST(SUM(o.par) AS NUMBER(15, 0)) AS offer_par
FROM   offerings o
WHERE  o.offer_date = :as_of_date
GROUP  BY o.cusip
