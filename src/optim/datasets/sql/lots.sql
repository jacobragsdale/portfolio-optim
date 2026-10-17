-- SQL Server. Open tax lots for a batch of accounts.
SELECT l.account_id,
       l.lot_id,
       l.cusip,
       CAST(l.par AS BIGINT)          AS par,
       CAST(l.cost_price AS FLOAT)    AS cost_price,
       l.acquired_date                AS acquired
FROM   tax_lots l
WHERE  l.account_id IN (SELECT value FROM OPENJSON(%(accounts)s))
  AND  l.par > 0
