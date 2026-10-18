-- Oracle. Reference data and analytics for every bond we can hold or buy.
SELECT s.cusip,
       s.issuer,
       s.sector,
       s.rating,
       a.dirty_price                      AS price,
       a.cost_bps,
       -- par amounts are not cast to whole numbers: a fractional one must fail validation, not be rounded
       s.min_piece,
       -- INCREMENT is reserved in Oracle, so the alias is quoted
       s.min_increment                    AS "INCREMENT",
       -- the minimum piece, where the security master records no minimum remaining
       COALESCE(s.min_remaining, s.min_piece) AS min_remaining
FROM   securities s
JOIN   analytics a ON a.cusip = s.cusip AND a.as_of = :as_of_date
