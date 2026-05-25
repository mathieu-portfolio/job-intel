SELECT offers.*
FROM offers
JOIN screening_results ON screening_results.offer_id = offers.id
WHERE screening_results.passed = 1
  AND screening_results.profile_path = ?
  AND NOT EXISTS (
      SELECT 1
      FROM ai_reviews
      WHERE ai_reviews.offer_id = offers.id
        AND ai_reviews.provider IS ?
        AND ai_reviews.model IS ?
        AND ai_reviews.profile_path = ?
  )
/*RECENT_FILTER*/
ORDER BY
    screening_results.score DESC,
    CASE WHEN offers.published_at IS NULL THEN 1 ELSE 0 END,
    offers.published_at DESC,
    offers.first_seen_at DESC
LIMIT ?;
