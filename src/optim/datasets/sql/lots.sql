-- SQL Server. Open tax lots for a batch of portfolios.
SELECT l.account_id                   AS portfolio_id,
       l.lot_id,
       l.cusip,
       l.par,
       CAST(l.cost_price AS FLOAT)    AS cost_price,
       l.acquired_date                AS acquired
FROM   tax_lots l
-- Typed: an untyped OPENJSON value is NVARCHAR, which turns an index seek on a VARCHAR key into a table scan.
WHERE  l.account_id IN (SELECT id FROM OPENJSON(%(portfolios)s) WITH (id VARCHAR(64) '$'))
  AND  l.par > 0
