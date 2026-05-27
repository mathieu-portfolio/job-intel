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
    profile_id TEXT NOT NULL DEFAULT 'default',
    profile_path TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    keep_flag INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS exploration_scopes (
    scope_key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    newest_id TEXT,
    oldest_id TEXT,
    last_explored_page INTEGER,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_path TEXT NOT NULL UNIQUE,
    name TEXT,
    profile_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ranking_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    model TEXT,
    profile_id TEXT NOT NULL DEFAULT 'default',
    profile_path TEXT NOT NULL,
    config_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scoring_presets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    weights_json TEXT NOT NULL,
    is_builtin INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS rankings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    offer_id INTEGER NOT NULL,
    algorithm TEXT NOT NULL,
    model TEXT,
    profile_id TEXT NOT NULL DEFAULT 'default',
    profile_path TEXT NOT NULL,
    score INTEGER NOT NULL,
    recommendation TEXT NOT NULL,
    summary TEXT NOT NULL,
    result_json TEXT NOT NULL,
    ranked_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES ranking_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(offer_id) REFERENCES offers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS screening_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id INTEGER NOT NULL,
    profile_id TEXT NOT NULL DEFAULT 'default',
    profile_path TEXT NOT NULL,
    score INTEGER NOT NULL,
    recommendation TEXT NOT NULL,
    threshold INTEGER NOT NULL,
    passed INTEGER NOT NULL,
    matched_signals_json TEXT NOT NULL,
    reasoning_json TEXT NOT NULL,
    screened_at TEXT NOT NULL,
    FOREIGN KEY(offer_id) REFERENCES offers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS offer_scores (
    offer_id INTEGER NOT NULL,
    profile_id TEXT NOT NULL DEFAULT 'default',
    preset_id TEXT NOT NULL,
    score INTEGER NOT NULL,
    signals_json TEXT,
    scored_at TEXT NOT NULL,
    PRIMARY KEY (offer_id, profile_id, preset_id),
    FOREIGN KEY(offer_id) REFERENCES offers(id) ON DELETE CASCADE,
    FOREIGN KEY(preset_id) REFERENCES scoring_presets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    screening_result_id INTEGER,
    offer_id INTEGER NOT NULL,
    provider TEXT,
    model TEXT,
    profile_id TEXT NOT NULL DEFAULT 'default',
    profile_path TEXT NOT NULL,
    preset_id TEXT NOT NULL DEFAULT 'balanced',
    score INTEGER NOT NULL,
    recommendation TEXT NOT NULL,
    summary TEXT NOT NULL,
    review_json TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    FOREIGN KEY(screening_result_id) REFERENCES screening_results(id) ON DELETE SET NULL,
    FOREIGN KEY(offer_id) REFERENCES offers(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_offers_newest
    ON offers(published_at DESC, first_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_offers_source_source_id
    ON offers(source, source_id)
    WHERE source_id IS NOT NULL;
-- Profile-scoped explored-offer indexes are created in the storage migration.
-- Keeping them out of the base schema lets existing databases add profile_id
-- before SQLite evaluates indexes that reference it.
CREATE INDEX IF NOT EXISTS idx_explored_offers_last_seen
    ON explored_offers(last_seen_at DESC);
