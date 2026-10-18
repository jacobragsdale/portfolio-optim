-- SQL Server. Positions, cash included, for a batch of portfolios: as of the start of the business day (`sod`), or
-- working (`working`: the start of the day plus what has traded and settled into the books since).
SELECT p.account_id                   AS portfolio_id,
       p.sleeve_id,
       p.security_id,
       p.quantity
FROM   positions p
WHERE  p.account_id IN (SELECT id FROM OPENJSON(%(portfolios)s) WITH (id VARCHAR(64) '$'))
  AND  p.as_of_date = %(as_of_date)s
  AND  p.basis = %(basis)s
  AND  p.quantity <> 0  -- cash can be overdrawn: a negative balance is cash the solve must raise
