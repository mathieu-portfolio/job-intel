SELECT explored_offers.id
FROM explored_offers
ORDER BY
    CASE WHEN explored_offers.keep_flag THEN 1 ELSE 0 END ASC,
    CASE
        WHEN EXISTS (
            SELECT 1
            FROM offers
            WHERE offers.source = explored_offers.provider
              AND (
                (explored_offers.external_id IS NOT NULL AND offers.source_id = explored_offers.external_id)
                OR (explored_offers.canonical_url IS NOT NULL AND offers.url = explored_offers.canonical_url)
              )
        )
        THEN 1 ELSE 0
    END ASC,
    explored_offers.first_seen_at ASC,
    explored_offers.id ASC
LIMIT ?;
