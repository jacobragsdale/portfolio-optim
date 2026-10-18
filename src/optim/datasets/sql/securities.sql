-- Oracle. Reference data, prices and analytics for every security a portfolio can hold or buy, cash included.
SELECT s.security_id,
       s.cusip,
       s.isin,
       s.asset_class,
       s.currency,
       s.issuer,
       s.sector,
       s.rating,
       s.maturity_date                    AS maturity,
       a.duration,
       -- prices and amounts are not cast to FLOAT: they arrive as exact decimals, and prep converts them for the solve
       a.clean_price                      AS price,
       a.accrued,
       s.price_per,
       a.cost_bps,
       -- par amounts are not cast to whole numbers: a fractional one must fail validation, not be rounded
       s.min_piece,
       -- INCREMENT is reserved in Oracle, so the alias is quoted
       s.min_increment                    AS "INCREMENT",
       s.min_remaining
FROM   securities s
JOIN   analytics a ON a.security_id = s.security_id AND a.as_of = :as_of_date
