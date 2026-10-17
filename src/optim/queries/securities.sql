-- Oracle. Reference data and analytics for every bond we can hold or buy.
SELECT s.cusip,
       s.issuer,
       s.sector,
       s.state,
       s.rating,
       s.tax_status,
       s.maturity_date                    AS maturity,
       a.dirty_price                      AS price,
       a.modified_duration                AS duration,
       a.cost_bps,
       CAST(s.min_piece AS NUMBER(15, 0)) AS min_piece,
       CAST(s.increment AS NUMBER(15, 0)) AS increment
FROM   securities s
JOIN   analytics a ON a.cusip = s.cusip AND a.as_of = :as_of
