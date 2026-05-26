DELETE FROM ai_reviews
WHERE offer_id = ?
  AND provider IS ?
  AND model IS ?
  AND profile_id = ?
  AND preset_id = ?;
