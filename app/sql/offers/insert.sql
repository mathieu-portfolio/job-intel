INSERT INTO offers (
    source, source_id, url, title, company, location, description,
    published_at, first_seen_at, last_seen_at, last_fetched_at, raw_json
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
