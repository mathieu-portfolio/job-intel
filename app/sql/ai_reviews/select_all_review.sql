SELECT ai_reviews.*, offers.title
FROM ai_reviews
JOIN offers ON offers.id = ai_reviews.offer_id
ORDER BY ai_reviews.reviewed_at DESC, ai_reviews.id DESC;
