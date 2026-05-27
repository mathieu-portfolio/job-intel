INSERT INTO ai_reviews (
    screening_result_id, offer_id, provider, model, profile_path, preset_id, score,
    recommendation, summary, review_json, reviewed_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
