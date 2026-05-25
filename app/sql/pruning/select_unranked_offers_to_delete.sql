SELECT offers.id
FROM offers
WHERE NOT EXISTS (
    SELECT 1 FROM rankings WHERE rankings.offer_id = offers.id
)
ORDER BY
    CASE WHEN offers.review_status = 'new' THEN 0 ELSE 1 END ASC,
    offers.first_seen_at ASC,
    offers.id ASC
LIMIT ?;
