SELECT
    (SELECT COUNT(*) FROM offers) AS offers_count,
    (
        SELECT COUNT(*)
        FROM rankings
        WHERE EXISTS (
            SELECT 1 FROM offers WHERE offers.id = rankings.offer_id
        )
    ) AS dependent_rankings_count;
