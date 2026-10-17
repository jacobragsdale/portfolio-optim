-- SQL Server. %(accounts)s arrives as a JSON array of account ids.
SELECT a.account_id,
       a.benchmark_id,
       CAST(a.cash AS FLOAT)          AS cash,
       CAST(t.federal_rate AS FLOAT)  AS federal_rate,
       CAST(t.state_rate AS FLOAT)    AS state_rate,
       CAST(t.st_gain_rate AS FLOAT)  AS st_gain_rate,
       CAST(t.lt_gain_rate AS FLOAT)  AS lt_gain_rate
FROM   accounts a
JOIN   account_tax_rates t ON t.account_id = a.account_id
WHERE  a.account_id IN (SELECT value FROM OPENJSON(%(accounts)s))
