UPDATE explored_offers
SET external_id = COALESCE(external_id, ?),
    canonical_url = COALESCE(canonical_url, ?),
    last_seen_at = ?,
    status = ?,
    reason = ?,
    keep_flag = CASE WHEN ? THEN 1 ELSE keep_flag END
WHERE id = ?;
