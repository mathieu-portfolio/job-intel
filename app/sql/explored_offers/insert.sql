INSERT INTO explored_offers (
    provider, external_id, canonical_url, first_seen_at, last_seen_at, status, reason, keep_flag
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?);
