UPDATE explored_offers
SET external_id = COALESCE(external_id, ?),
    canonical_url = COALESCE(canonical_url, ?),
    last_seen_at = ?,
    status = ?,
    reason = ?
WHERE id = ?;
