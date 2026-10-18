-- SQL Server, which calls a portfolio an account. %(portfolios)s arrives as a JSON array of portfolio ids.
SELECT a.account_id                   AS portfolio_id,
       a.base_currency                AS currency,
       a.target_market_value          AS target_value
FROM   accounts a
-- Typed: an untyped OPENJSON value is NVARCHAR, which turns an index seek on a VARCHAR key into a table scan.
WHERE  a.account_id IN (SELECT id FROM OPENJSON(%(portfolios)s) WITH (id VARCHAR(64) '$'))
