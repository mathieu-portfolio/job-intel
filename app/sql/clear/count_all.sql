SELECT
    (SELECT COUNT(*) FROM explored_offers) AS explored_count,
    (SELECT COUNT(*) FROM offers) AS offers_count,
    (SELECT COUNT(*) FROM rankings) AS rankings_count,
    (SELECT COUNT(*) FROM ranking_runs) AS ranking_runs_count;
