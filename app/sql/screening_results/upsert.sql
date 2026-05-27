INSERT INTO screening_results (
    offer_id, profile_id, score, recommendation, threshold, passed,
    matched_signals_json, reasoning_json, screened_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(offer_id, profile_id) DO UPDATE SET
    score = excluded.score,
    recommendation = excluded.recommendation,
    threshold = excluded.threshold,
    passed = excluded.passed,
    matched_signals_json = excluded.matched_signals_json,
    reasoning_json = excluded.reasoning_json,
    screened_at = excluded.screened_at;
