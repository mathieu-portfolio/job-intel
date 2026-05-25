DELETE FROM ranking_runs
WHERE NOT EXISTS (
    SELECT 1 FROM rankings WHERE rankings.run_id = ranking_runs.id
);
