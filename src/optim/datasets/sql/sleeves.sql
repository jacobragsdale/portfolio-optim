-- SQL Server. The sleeves of a batch of portfolios: eligibility list, benchmark and target weight each.
SELECT s.account_id                   AS portfolio_id,
       s.sleeve_id,
       s.universe_id,
       s.benchmark_id,
       s.target_weight
FROM   account_sleeves s
WHERE  s.account_id IN (SELECT id FROM OPENJSON(%(portfolios)s) WITH (id VARCHAR(64) '$'))
