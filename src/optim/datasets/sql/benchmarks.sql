-- Oracle. Constituent weights for every benchmark.
SELECT b.benchmark_id,
       b.security_id,
       b.weight
FROM   benchmark_weights b
WHERE  b.as_of = :as_of_date
