INSERT INTO rankings (
    run_id, offer_id, algorithm, model, profile_id, score,
    recommendation, summary, result_json, ranked_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
