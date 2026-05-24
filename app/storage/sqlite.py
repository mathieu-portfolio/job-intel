from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.models.evaluation import Recommendation
from app.models.job import JobOffer
from app.sql import load_sql


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


VALID_REVIEW_STATUSES = {"new", "saved", "skipped", "applied"}


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


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as connection:
        connection.executescript(load_sql("schema/init.sql"))
        columns = {
            row["name"]
            for row in connection.execute(load_sql("schema/offers_columns.sql")).fetchall()
        }
        if "review_status" not in columns:
            connection.execute(load_sql("schema/add_review_status.sql"))


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
                load_sql("offers/select_by_url.sql"),
                (url,),
            ).fetchone()
            raw_json = json.dumps(
                job.raw_json if job.raw_json is not None else job.model_dump(mode="json"),
                ensure_ascii=False,
            )
            if existing is None:
                inserted += 1
                connection.execute(
                    load_sql("offers/insert.sql"),
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
                    load_sql("offers/update.sql"),
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
        sql = load_sql("offers/select_unranked.sql").replace("/*RECENT_FILTER*/", recent_clause)
        rows = connection.execute(sql, params).fetchall()

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
            load_sql("ranking_runs/insert.sql"),
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
            load_sql("rankings/delete_existing.sql"),
            (offer_id, algorithm, model, profile_path),
        )
        connection.execute(
            load_sql("rankings/insert.sql"),
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


def update_offer_status(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    offer_id: int,
    status: str,
) -> None:
    if status not in VALID_REVIEW_STATUSES:
        raise ValueError(f"Unsupported offer status: {status}")
    init_db(db_path)
    with _connect(db_path) as connection:
        connection.execute(
            load_sql("offers/update_status.sql"),
            (status, offer_id),
        )


def clear_rankings(db_path: Path = DEFAULT_DB_PATH) -> None:
    init_db(db_path)
    with _connect(db_path) as connection:
        connection.execute(load_sql("rankings/delete_all.sql"))


def list_ranked_offers(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    recommendation: str | None = None,
    status: str | None = None,
    source: str | None = None,
    ranking_mode: str | None = None,
    only_recent_days: int | None = None,
    ai_only: bool = False,
    sort: str = "score_desc",
    limit: int = 100,
) -> list[dict[str, Any]]:
    init_db(db_path)
    clauses: list[str] = []
    params: list[Any] = []
    if recommendation:
        clauses.append("rankings.recommendation = ?")
        params.append(recommendation)
    if status:
        clauses.append("offers.review_status = ?")
        params.append(status)
    if source:
        clauses.append("offers.source = ?")
        params.append(source)
    if ranking_mode:
        clauses.append("rankings.algorithm = ?")
        params.append(ranking_mode)
    if ai_only:
        clauses.append(
            "("
            "rankings.algorithm = 'ai' "
            "OR (rankings.algorithm = 'hybrid' "
            "AND rankings.result_json NOT LIKE '%\"raw_ai_evaluation\": null%')"
            ")"
        )
    if only_recent_days is not None:
        cutoff = (datetime.now() - timedelta(days=only_recent_days)).isoformat(timespec="seconds")
        clauses.append("COALESCE(offers.published_at, offers.first_seen_at) >= ?")
        params.append(cutoff)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order_by = {
        "score_desc": "rankings.score DESC, rankings.ranked_at DESC",
        "ranked_newest": "rankings.ranked_at DESC, rankings.score DESC",
        "offer_newest": "COALESCE(offers.published_at, offers.first_seen_at) DESC, rankings.score DESC",
        "recommendation": "rankings.recommendation ASC, rankings.score DESC",
        "status": "offers.review_status ASC, rankings.score DESC",
        "source": "offers.source ASC, rankings.score DESC",
    }.get(sort, "rankings.score DESC, rankings.ranked_at DESC")

    params.append(limit)
    with _connect(db_path) as connection:
        sql = (
            load_sql("rankings/select_review.sql")
            .replace("/*WHERE_CLAUSE*/", where_sql)
            .replace("/*ORDER_BY*/", order_by)
        )
        rows = connection.execute(sql, params).fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        try:
            result_json = json.loads(row["result_json"])
        except json.JSONDecodeError:
            result_json = {}
        results.append({**dict(row), "result": result_json})
    return results


def get_review_filter_options(db_path: Path = DEFAULT_DB_PATH) -> dict[str, list[str]]:
    init_db(db_path)
    with _connect(db_path) as connection:
        sources = [
            row["source"]
            for row in connection.execute(load_sql("rankings/select_sources.sql")).fetchall()
        ]
        algorithms = [
            row["algorithm"]
            for row in connection.execute(load_sql("rankings/select_algorithms.sql")).fetchall()
        ]
    return {
        "sources": sources,
        "ranking_modes": algorithms,
        "statuses": sorted(VALID_REVIEW_STATUSES),
        "recommendations": ["high", "medium", "low", "skip"],
    }
