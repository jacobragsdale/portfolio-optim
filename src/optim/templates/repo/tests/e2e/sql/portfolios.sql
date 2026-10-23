-- SQL Server, QA. The portfolios the smoke cases decide, picked by what they hold rather than by fixed ids, which drift:
-- three that hold more than cash. Change it to pick what each model needs.
SELECT TOP 3 p.account_id AS portfolio_id
FROM   positions p
GROUP  BY p.account_id
HAVING COUNT(*) > 1
ORDER  BY p.account_id
