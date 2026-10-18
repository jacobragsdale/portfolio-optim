-- Oracle. Bonds dealers are offering today.
SELECT o.cusip,
       SUM(o.par)                        AS offer_par
FROM   offerings o
WHERE  o.offer_date = :as_of_date
GROUP  BY o.cusip
