UPDATE offers
SET source = ?,
    source_id = ?,
    title = ?,
    company = ?,
    location = ?,
    description = ?,
    published_at = ?,
    last_seen_at = ?,
    last_fetched_at = ?,
    raw_json = ?
WHERE url = ?;
