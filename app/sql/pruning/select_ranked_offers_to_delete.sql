SELECT offers.id
FROM offers
WHERE EXISTS (
    SELECT 1 FROM rankings WHERE rankings.offer_id = offers.id
)
ORDER BY
    CASE WHEN offers.review_status = 'new' THEN 0 ELSE 1 END ASC,
    (
        SELECT MIN(rankings.ranked_at)
        FROM rankings
        WHERE rankings.offer_id = offers.id
    ) ASC,
    offers.id ASC
LIMIT ?;
