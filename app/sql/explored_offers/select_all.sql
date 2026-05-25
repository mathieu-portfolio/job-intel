SELECT provider, external_id, canonical_url, first_seen_at, last_seen_at, status, reason, keep_flag
FROM explored_offers
ORDER BY id;
