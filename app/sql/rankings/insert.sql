INSERT INTO rankings (
    run_id, offer_id, algorithm, model, profile_id, profile_path, score,
    recommendation, summary, result_json, ranked_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
