-- SQL Server. External cash flows to apply in a batch of portfolios' solves: contributions positive, withdrawals
-- negative.
SELECT f.account_id                   AS portfolio_id,
       f.flow_id,
       f.amount
FROM   pending_cash_flows f
WHERE  f.account_id IN (SELECT id FROM OPENJSON(%(portfolios)s) WITH (id VARCHAR(64) '$'))
  AND  f.value_date <= %(as_of_date)s
