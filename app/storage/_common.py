from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from app.models.evaluation import Recommendation
from app.models.evaluation import RuleEvaluation
from app.models.job import JobOffer
from app.filtering.presets import BUILTIN_SCORING_PRESETS, ScoringPreset
from app.filtering.rules import load_rule_scoring_config, parse_rule_scoring_config
from app.sql import load_sql


DEFAULT_DB_PATH = Path("data/job_intel.sqlite")
DEFAULT_SCORING_PRESET_ID = "balanced"
DEFAULT_EXPLORED_CAPACITY = 10_000
DEFAULT_UNRANKED_CAPACITY = 1_000
DEFAULT_RANKED_CAPACITY = 300
ClearScope = Literal["rankings", "offers", "explored", "all"]
VALID_CLEAR_SCOPES: set[str] = {"rankings", "offers", "explored", "all"}
VALID_REVIEW_STATUSES: set[str] = {"new", "saved", "skipped", "applied"}


@dataclass(frozen=True)
class StoredOffer:
    id: int
    job: JobOffer


@dataclass(frozen=True)
class UpsertStats:
    fetched: int
    inserted: int
    updated: int
    skipped_existing: int = 0
    pages_scanned: int = 0
    explored: int = 0
    newly_explored: int = 0
    already_seen: int = 0
    filtered_out: int = 0
    errors: int = 0


@dataclass(frozen=True)
class ExploredOfferRecord:
    provider: str
    external_id: str | None
    canonical_url: str | None
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class StorageCounts:
    explored: int
    unranked: int
    ranked: int


@dataclass(frozen=True)
class PruneStats:
    deleted_explored: int
    deleted_unranked: int
    deleted_ranked: int
    before: StorageCounts
    after: StorageCounts


@dataclass(frozen=True)
class ClearPlan:
    scope: ClearScope
    explored: int = 0
    offers: int = 0
    rankings: int = 0
    ranking_runs: int = 0


@dataclass(frozen=True)
class ExplorationMetadata:
    scope_key: str
    newest_id: str | None
    oldest_id: str | None
    last_explored_page: int | None
    updated_at: str


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def open_connection(db_path: Path = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    return _connect(db_path)


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as connection:
        connection.executescript(load_sql("schema/init.sql"))
        _migrate_multi_preset_scores(connection)
        _migrate_exploration_metadata(connection)
        offer_columns = {
            row["name"]
            for row in connection.execute(load_sql("schema/offers_columns.sql")).fetchall()
        }
        if "review_status" not in offer_columns:
            connection.execute(load_sql("schema/add_review_status.sql"))
        explored_columns = {
            row["name"]
            for row in connection.execute(load_sql("schema/explored_offers_columns.sql")).fetchall()
        }
        if "keep_flag" not in explored_columns:
            connection.execute(load_sql("schema/add_explored_keep_flag.sql"))
        _seed_builtin_scoring_presets(connection)
        _backfill_balanced_scores(connection)


def _migrate_exploration_metadata(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS exploration_scopes (
            scope_key TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            scope_json TEXT NOT NULL,
            newest_id TEXT,
            oldest_id TEXT,
            last_explored_page INTEGER,
            updated_at TEXT NOT NULL
        );
        """
    )


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name});").fetchall()
    }


def _migrate_multi_preset_scores(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS scoring_presets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            weights_json TEXT NOT NULL,
            is_builtin INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS offer_scores (
            offer_id INTEGER NOT NULL,
            preset_id TEXT NOT NULL,
            score INTEGER NOT NULL,
            signals_json TEXT,
            scored_at TEXT NOT NULL,
            PRIMARY KEY (offer_id, preset_id),
            FOREIGN KEY(offer_id) REFERENCES offers(id) ON DELETE CASCADE,
            FOREIGN KEY(preset_id) REFERENCES scoring_presets(id) ON DELETE CASCADE
        );
        """
    )
    offer_score_columns = _table_columns(connection, "offer_scores")
    if "preset_id" not in offer_score_columns:
        connection.execute(
            "ALTER TABLE offer_scores ADD COLUMN preset_id TEXT NOT NULL DEFAULT 'balanced';"
        )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS offer_ai_reviews (
            offer_id INTEGER NOT NULL,
            preset_id TEXT NOT NULL DEFAULT 'balanced',
            ai_score INTEGER,
            verdict TEXT,
            rationale TEXT,
            reviewed_at TEXT NOT NULL,
            PRIMARY KEY (offer_id, preset_id),
            FOREIGN KEY(offer_id) REFERENCES offers(id) ON DELETE CASCADE
        );
        """
    )
    offer_ai_review_columns = _table_columns(connection, "offer_ai_reviews")
    if "preset_id" not in offer_ai_review_columns:
        connection.execute(
            "ALTER TABLE offer_ai_reviews ADD COLUMN preset_id TEXT NOT NULL DEFAULT 'balanced';"
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_offer_scores_preset_score ON offer_scores(preset_id, score DESC);"
    )
    ai_columns = _table_columns(connection, "ai_reviews")
    if "preset_id" not in ai_columns:
        connection.execute(
            "ALTER TABLE ai_reviews ADD COLUMN preset_id TEXT NOT NULL DEFAULT 'balanced';"
        )
    connection.execute("DROP INDEX IF EXISTS idx_ai_reviews_unique_offer_provider_model_profile;")
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_reviews_unique_offer_provider_model_profile_preset
        ON ai_reviews(offer_id, COALESCE(provider, ''), COALESCE(model, ''), profile_path, preset_id);
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_reviews_preset_lookup
        ON ai_reviews(profile_path, preset_id, provider, model, score DESC);
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO offer_ai_reviews (
            offer_id, preset_id, ai_score, verdict, rationale, reviewed_at
        )
        SELECT
            offer_id,
            COALESCE(preset_id, 'balanced'),
            score,
            recommendation,
            summary,
            reviewed_at
        FROM ai_reviews;
        """
    )


def _seed_builtin_scoring_presets(connection: sqlite3.Connection) -> None:
    for preset in BUILTIN_SCORING_PRESETS:
        connection.execute(
            """
            INSERT INTO scoring_presets (
                id, name, description, weights_json, is_builtin, enabled
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                weights_json = excluded.weights_json,
                is_builtin = excluded.is_builtin;
            """,
            (
                preset.id,
                preset.name,
                preset.description,
                json.dumps(preset.weights.model_dump(mode="json"), ensure_ascii=False),
                1 if preset.is_builtin else 0,
                1 if preset.enabled else 0,
            ),
        )


def _backfill_balanced_scores(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO offer_scores (
            offer_id, preset_id, score, signals_json, scored_at
        )
        SELECT
            offer_id,
            'balanced',
            score,
            matched_signals_json,
            screened_at
        FROM screening_results;
        """
    )


# Private implementation modules intentionally use `from app.storage._common import *`.
# Include underscored helpers in star imports so split modules keep the old monolith behavior.
__all__ = [name for name in globals() if not name.startswith("__")]
