from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.models.evaluation import Recommendation
from app.models.job import JobOffer


DEFAULT_DB_PATH = Path("data/job_intel.sqlite")


@dataclass(frozen=True)
class StoredOffer:
    id: int
    job: JobOffer


@dataclass(frozen=True)
class UpsertStats:
    fetched: int
    inserted: int
    updated: int


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as connection:
        connection.executescript(
            """
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
            CREATE INDEX IF NOT EXISTS idx_rankings_lookup
                ON rankings(algorithm, model, profile_path);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_rankings_unique_offer_algorithm_model_profile
                ON rankings(offer_id, algorithm, COALESCE(model, ''), profile_path);
            """
        )


def upsert_offers(
    jobs: list[JobOffer],
    *,
    db_path: Path = DEFAULT_DB_PATH,
    fetched_at: str | None = None,
) -> UpsertStats:
    init_db(db_path)
    fetched_at = fetched_at or _now_iso()
    inserted = 0
    updated = 0

    with _connect(db_path) as connection:
        for job in jobs:
            url = str(job.url)
            existing = connection.execute(
                "SELECT id FROM offers WHERE url = ?",
                (url,),
            ).fetchone()
            raw_json = json.dumps(
                job.raw_json if job.raw_json is not None else job.model_dump(mode="json"),
                ensure_ascii=False,
            )
            if existing is None:
                inserted += 1
                connection.execute(
                    """
                    INSERT INTO offers (
                        source, source_id, url, title, company, location, description,
                        published_at, first_seen_at, last_seen_at, last_fetched_at, raw_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job.source,
                        job.source_id,
                        url,
                        job.title,
                        job.company,
                        job.location,
                        job.description,
                        job.published_at,
                        fetched_at,
                        fetched_at,
                        fetched_at,
                        raw_json,
                    ),
                )
            else:
                updated += 1
                connection.execute(
                    """
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
                    WHERE url = ?
                    """,
                    (
                        job.source,
                        job.source_id,
                        job.title,
                        job.company,
                        job.location,
                        job.description,
                        job.published_at,
                        fetched_at,
                        fetched_at,
                        raw_json,
                        url,
                    ),
                )

    return UpsertStats(fetched=len(jobs), inserted=inserted, updated=updated)


def _job_from_offer_row(row: sqlite3.Row) -> JobOffer:
    raw_json: dict[str, Any]
    try:
        raw_json = json.loads(row["raw_json"])
    except json.JSONDecodeError:
        raw_json = {}
    return JobOffer(
        source=row["source"],
        source_id=row["source_id"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        url=row["url"],
        description=row["description"] or "",
        published_at=row["published_at"],
        tags=raw_json.get("tags") or [],
        remote=raw_json.get("remote"),
        salary=raw_json.get("salary"),
        raw_json=raw_json,
    )


def select_unranked_offers(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    algorithm: str,
    model: str | None,
    profile_path: str,
    limit: int,
    only_recent_days: int | None = None,
) -> list[StoredOffer]:
    init_db(db_path)
    params: list[Any] = [algorithm, model, profile_path]
    recent_clause = ""
    if only_recent_days is not None:
        cutoff = (datetime.now() - timedelta(days=only_recent_days)).isoformat(timespec="seconds")
        recent_clause = "AND COALESCE(published_at, first_seen_at) >= ?"
        params.append(cutoff)
    params.append(limit)

    with _connect(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT offers.*
            FROM offers
            WHERE NOT EXISTS (
                SELECT 1
                FROM rankings
                WHERE rankings.offer_id = offers.id
                  AND rankings.algorithm = ?
                  AND rankings.model IS ?
                  AND rankings.profile_path = ?
            )
            {recent_clause}
            ORDER BY
                CASE WHEN published_at IS NULL THEN 1 ELSE 0 END,
                published_at DESC,
                first_seen_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

    return [StoredOffer(id=row["id"], job=_job_from_offer_row(row)) for row in rows]


def create_ranking_run(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    started_at: str,
    algorithm: str,
    model: str | None,
    profile_path: str,
    config: dict[str, Any],
) -> int:
    init_db(db_path)
    with _connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO ranking_runs (started_at, algorithm, model, profile_path, config_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                started_at,
                algorithm,
                model,
                profile_path,
                json.dumps(config, ensure_ascii=False),
            ),
        )
        return int(cursor.lastrowid)


def save_ranking(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    run_id: int,
    offer_id: int,
    algorithm: str,
    model: str | None,
    profile_path: str,
    score: int,
    recommendation: Recommendation,
    summary: str,
    result: dict[str, Any],
    ranked_at: str | None = None,
) -> None:
    init_db(db_path)
    ranked_at = ranked_at or _now_iso()
    with _connect(db_path) as connection:
        connection.execute(
            """
            DELETE FROM rankings
            WHERE offer_id = ?
              AND algorithm = ?
              AND model IS ?
              AND profile_path = ?
            """,
            (offer_id, algorithm, model, profile_path),
        )
        connection.execute(
            """
            INSERT INTO rankings (
                run_id, offer_id, algorithm, model, profile_path, score,
                recommendation, summary, result_json, ranked_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                offer_id,
                algorithm,
                model,
                profile_path,
                score,
                recommendation,
                summary,
                json.dumps(result, ensure_ascii=False),
                ranked_at,
            ),
        )
