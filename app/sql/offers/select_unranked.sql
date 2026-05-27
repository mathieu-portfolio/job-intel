SELECT offers.*
FROM offers
WHERE NOT EXISTS (
    SELECT 1
    FROM rankings
    WHERE rankings.offer_id = offers.id
      AND rankings.algorithm = ?
      AND rankings.model IS ?
      AND rankings.profile_id = ?
)
/*RECENT_FILTER*/
ORDER BY
    CASE WHEN published_at IS NULL THEN 1 ELSE 0 END,
    published_at DESC,
    first_seen_at DESC
LIMIT ?;
