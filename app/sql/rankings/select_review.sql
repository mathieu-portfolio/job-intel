SELECT
    rankings.id AS ranking_id,
    rankings.offer_id,
    rankings.algorithm,
    rankings.model,
    rankings.profile_id,
    rankings.score,
    rankings.recommendation,
    rankings.summary,
    rankings.result_json,
    rankings.ranked_at,
    offers.source,
    offers.url,
    offers.title,
    offers.company,
    offers.location,
    offers.published_at,
    offers.first_seen_at,
    offers.review_status
FROM rankings
JOIN offers ON offers.id = rankings.offer_id
/*WHERE_CLAUSE*/
ORDER BY /*ORDER_BY*/
LIMIT ?;
