-- Oracle. Every eligibility list: what each list's sleeves may buy, and how much is available where the list is an
-- inventory.
SELECT e.universe_id,
       e.security_id,
       e.available_quantity               AS available
FROM   eligibility e
WHERE  e.as_of = :as_of_date
