SELECT
    offers.id AS offer_id,
    offers.source,
    offers.url,
    offers.title,
    offers.company,
    offers.location,
    offers.description,
    offers.published_at,
    offers.first_seen_at,
    offers.last_seen_at,
    offers.last_fetched_at
FROM offers
WHERE NOT EXISTS (
    SELECT 1
    FROM rankings
    WHERE rankings.offer_id = offers.id
)
/*FILTER_CLAUSE*/
ORDER BY
    offers.last_fetched_at DESC,
    CASE WHEN offers.published_at IS NULL THEN 1 ELSE 0 END,
    offers.published_at DESC,
    offers.first_seen_at DESC
LIMIT ?;
