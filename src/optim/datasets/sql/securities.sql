-- Oracle. Reference data and analytics for every bond we can hold or buy.
SELECT s.cusip,
       s.issuer,
       s.sector,
       s.rating,
       -- 'taxable', or 'state_exempt' for Treasuries and other bonds whose interest no state taxes
       s.tax_status,
       s.maturity_date                    AS maturity,
       a.dirty_price                      AS price,
       a.modified_duration                AS duration,
       a.cost_bps,
       CAST(s.min_piece AS NUMBER(15, 0)) AS min_piece,
       -- INCREMENT is reserved in Oracle, so the alias is quoted
       CAST(s.min_increment AS NUMBER(15, 0)) AS "INCREMENT",
       -- the minimum piece, where the security master records no minimum remaining
       CAST(COALESCE(s.min_remaining, s.min_piece) AS NUMBER(15, 0)) AS min_remaining
FROM   securities s
JOIN   analytics a ON a.cusip = s.cusip AND a.as_of = :as_of
