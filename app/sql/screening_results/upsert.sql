INSERT INTO screening_results (
    offer_id, profile_id, profile_path, score, recommendation, threshold, passed,
    matched_signals_json, reasoning_json, category_scores_json, screened_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(offer_id, profile_id) DO UPDATE SET
    profile_path = excluded.profile_path,
    score = excluded.score,
    recommendation = excluded.recommendation,
    threshold = excluded.threshold,
    passed = excluded.passed,
    matched_signals_json = excluded.matched_signals_json,
    reasoning_json = excluded.reasoning_json,
    category_scores_json = excluded.category_scores_json,
    screened_at = excluded.screened_at;
