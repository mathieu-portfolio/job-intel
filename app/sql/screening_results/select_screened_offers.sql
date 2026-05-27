SELECT offers.*
FROM offers
JOIN offer_scores ON offer_scores.offer_id = offers.id
WHERE offer_scores.profile_id = ?
  AND offer_scores.preset_id = ?
/*MIN_SCORE_FILTER*/  AND NOT EXISTS (
      SELECT 1
      FROM ai_reviews
      WHERE ai_reviews.offer_id = offers.id
        AND ai_reviews.provider IS ?
        AND ai_reviews.model IS ?
        AND ai_reviews.profile_id = ?
        AND ai_reviews.preset_id = ?
  )
/*RECENT_FILTER*/
ORDER BY
    offer_scores.score DESC,
    CASE WHEN offers.published_at IS NULL THEN 1 ELSE 0 END,
    offers.published_at DESC,
    offers.first_seen_at DESC
LIMIT ?;
