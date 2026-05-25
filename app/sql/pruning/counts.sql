SELECT
    (SELECT COUNT(*) FROM explored_offers) AS explored_count,
    (
        SELECT COUNT(*)
        FROM offers
        WHERE NOT EXISTS (
            SELECT 1 FROM rankings WHERE rankings.offer_id = offers.id
        )
    ) AS unranked_count,
    (SELECT COUNT(DISTINCT offer_id) FROM rankings) AS ranked_count;
