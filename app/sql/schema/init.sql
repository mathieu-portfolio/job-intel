CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    description TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_fetched_at TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS explored_offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    external_id TEXT,
    canonical_url TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT
);

CREATE TABLE IF NOT EXISTS ranking_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    model TEXT,
    profile_path TEXT NOT NULL,
    config_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rankings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    offer_id INTEGER NOT NULL,
    algorithm TEXT NOT NULL,
    model TEXT,
    profile_path TEXT NOT NULL,
    score INTEGER NOT NULL,
    recommendation TEXT NOT NULL,
    summary TEXT NOT NULL,
    result_json TEXT NOT NULL,
    ranked_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES ranking_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(offer_id) REFERENCES offers(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_offers_newest
    ON offers(published_at DESC, first_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_offers_source_source_id
    ON offers(source, source_id)
    WHERE source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_explored_offers_provider_external_id
    ON explored_offers(provider, external_id)
    WHERE external_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_explored_offers_provider_canonical_url
    ON explored_offers(provider, canonical_url)
    WHERE canonical_url IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_explored_offers_last_seen
    ON explored_offers(last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_rankings_lookup
    ON rankings(algorithm, model, profile_path);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rankings_unique_offer_algorithm_model_profile
    ON rankings(offer_id, algorithm, COALESCE(model, ''), profile_path);
