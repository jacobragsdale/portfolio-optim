-- SQL Server. Bonds a batch of portfolios sold at a loss in the 30 days to the business date: buying one back within 30
-- days of a loss sale makes it a wash sale, which disallows the loss.
SELECT DISTINCT s.account_id          AS portfolio_id,
       s.cusip,
       s.sold_date                    AS sold
FROM   tax_lot_sales s
-- Typed: an untyped OPENJSON value is NVARCHAR, which turns an index seek on a VARCHAR key into a table scan.
WHERE  s.account_id IN (SELECT id FROM OPENJSON(%(portfolios)s) WITH (id VARCHAR(64) '$'))
  AND  s.sold_date BETWEEN DATEADD(day, -30, %(as_of_date)s) AND %(as_of_date)s
  AND  s.sale_price < s.cost_price
