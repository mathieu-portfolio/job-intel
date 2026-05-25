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
from app.models.job import JobOffer
from app.sql import load_sql


DEFAULT_DB_PATH = Path("data/job_intel.sqlite")
DEFAULT_EXPLORED_CAPACITY = 10_000
DEFAULT_UNRANKED_CAPACITY = 1_000
DEFAULT_RANKED_CAPACITY = 300
ClearScope = Literal["rankings", "offers", "explored", "all"]
VALID_CLEAR_SCOPES: set[str] = {"rankings", "offers", "explored", "all"}


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


def _canonical_url(job: JobOffer) -> str:
    return str(job.url)


def has_explored_offer(
    *,
    provider: str,
    external_id: str | None,
    canonical_url: str | None,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    init_db(db_path)
    with _connect(db_path) as connection:
        row = connection.execute(
            load_sql("explored_offers/select_by_identity.sql"),
            (provider, external_id, canonical_url, external_id),
        ).fetchone()
    return row is not None


def record_explored_offer(
    *,
    provider: str,
    external_id: str | None,
    canonical_url: str | None,
    status: str,
    reason: str | None = None,
    keep_flag: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
    seen_at: str | None = None,
) -> None:
    init_db(db_path)
    seen_at = seen_at or _now_iso()
    with _connect(db_path) as connection:
        row = connection.execute(
            load_sql("explored_offers/select_by_identity.sql"),
            (provider, external_id, canonical_url, external_id),
        ).fetchone()
        if row is None:
            connection.execute(
                load_sql("explored_offers/insert.sql"),
                (
                    provider,
                    external_id,
                    canonical_url,
                    seen_at,
                    seen_at,
                    status,
                    reason,
                    1 if keep_flag else 0,
                ),
            )
        else:
            connection.execute(
                load_sql("explored_offers/update.sql"),
                (external_id, canonical_url, seen_at, status, reason, 1 if keep_flag else 0, row["id"]),
            )


def record_explored_job(
    job: JobOffer,
    *,
    status: str,
    reason: str | None = None,
    keep_flag: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
    seen_at: str | None = None,
) -> None:
    record_explored_offer(
        provider=job.source,
        external_id=job.source_id,
        canonical_url=_canonical_url(job),
        status=status,
        reason=reason,
        keep_flag=keep_flag,
        db_path=db_path,
        seen_at=seen_at,
    )


def find_existing_offer_id(
    job: JobOffer,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> int | None:
    init_db(db_path)
    url = _canonical_url(job)
    with _connect(db_path) as connection:
        row = connection.execute(
            load_sql("offers/select_existing_identity.sql"),
            (job.source, job.source_id, url, job.source, job.source_id),
        ).fetchone()
    return int(row["id"]) if row is not None else None


def find_existing_offer_id_by_url(
    canonical_url: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> int | None:
    init_db(db_path)
    with _connect(db_path) as connection:
        row = connection.execute(
            load_sql("offers/select_by_identity_url.sql"),
            (canonical_url,),
        ).fetchone()
    return int(row["id"]) if row is not None else None


def list_explored_offers(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path)
    with _connect(db_path) as connection:
        rows = connection.execute(load_sql("explored_offers/select_all.sql")).fetchall()
    return [dict(row) for row in rows]


def _select_ids_to_delete(
    connection: sqlite3.Connection,
    sql_name: str,
    count: int,
) -> list[int]:
    if count <= 0:
        return []
    rows = connection.execute(load_sql(sql_name), (count,)).fetchall()
    return [int(row["id"]) for row in rows]


def _delete_ids(
    connection: sqlite3.Connection,
    sql_name: str,
    ids: list[int],
) -> int:
    if not ids:
        return 0
    placeholders = ", ".join(["?"] * len(ids))
    sql = load_sql(sql_name).replace("/*IDS*/", placeholders)
    cursor = connection.execute(sql, ids)
    return int(cursor.rowcount if cursor.rowcount is not None else len(ids))


def get_storage_counts(db_path: Path = DEFAULT_DB_PATH) -> StorageCounts:
    init_db(db_path)
    with _connect(db_path) as connection:
        row = connection.execute(load_sql("pruning/counts.sql")).fetchone()
    return StorageCounts(
        explored=int(row["explored_count"]),
        unranked=int(row["unranked_count"]),
        ranked=int(row["ranked_count"]),
    )


def prune_storage(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    explored_capacity: int = DEFAULT_EXPLORED_CAPACITY,
    unranked_capacity: int = DEFAULT_UNRANKED_CAPACITY,
    ranked_capacity: int = DEFAULT_RANKED_CAPACITY,
) -> PruneStats:
    if explored_capacity < 0 or unranked_capacity < 0 or ranked_capacity < 0:
        raise ValueError("Storage capacities must be zero or greater.")

    init_db(db_path)
    with _connect(db_path) as connection:
        before_row = connection.execute(load_sql("pruning/counts.sql")).fetchone()
        before = StorageCounts(
            explored=int(before_row["explored_count"]),
            unranked=int(before_row["unranked_count"]),
            ranked=int(before_row["ranked_count"]),
        )

        explored_overage = max(0, before.explored - explored_capacity)
        explored_ids = _select_ids_to_delete(
            connection,
            "pruning/select_explored_to_delete.sql",
            explored_overage,
        )
        deleted_explored = _delete_ids(
            connection,
            "pruning/delete_explored_by_ids.sql",
            explored_ids,
        )

        unranked_overage = max(0, before.unranked - unranked_capacity)
        unranked_offer_ids = _select_ids_to_delete(
            connection,
            "pruning/select_unranked_offers_to_delete.sql",
            unranked_overage,
        )
        deleted_unranked = _delete_ids(
            connection,
            "pruning/delete_offers_by_ids.sql",
            unranked_offer_ids,
        )

        ranked_overage = max(0, before.ranked - ranked_capacity)
        ranked_offer_ids = _select_ids_to_delete(
            connection,
            "pruning/select_ranked_offers_to_delete.sql",
            ranked_overage,
        )
        deleted_ranked = _delete_ids(
            connection,
            "pruning/delete_offers_by_ids.sql",
            ranked_offer_ids,
        )
        connection.execute(load_sql("pruning/delete_orphaned_ranking_runs.sql"))

        after_row = connection.execute(load_sql("pruning/counts.sql")).fetchone()
        after = StorageCounts(
            explored=int(after_row["explored_count"]),
            unranked=int(after_row["unranked_count"]),
            ranked=int(after_row["ranked_count"]),
        )

    return PruneStats(
        deleted_explored=deleted_explored,
        deleted_unranked=deleted_unranked,
        deleted_ranked=deleted_ranked,
        before=before,
        after=after,
    )


def _validate_clear_scope(scope: str) -> ClearScope:
    if scope not in VALID_CLEAR_SCOPES:
        valid = ", ".join(sorted(VALID_CLEAR_SCOPES))
        raise ValueError(f"Unsupported clear scope: {scope}. Expected one of: {valid}.")
    return scope  # type: ignore[return-value]


def get_clear_plan(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    scope: str,
) -> ClearPlan:
    clear_scope = _validate_clear_scope(scope)
    init_db(db_path)
    with _connect(db_path) as connection:
        if clear_scope == "rankings":
            row = connection.execute(load_sql("clear/count_rankings.sql")).fetchone()
            return ClearPlan(scope=clear_scope, rankings=int(row["rankings_count"]))
        if clear_scope == "offers":
            row = connection.execute(load_sql("clear/count_offers.sql")).fetchone()
            return ClearPlan(
                scope=clear_scope,
                offers=int(row["offers_count"]),
                rankings=int(row["dependent_rankings_count"]),
            )
        if clear_scope == "explored":
            row = connection.execute(load_sql("clear/count_explored.sql")).fetchone()
            return ClearPlan(scope=clear_scope, explored=int(row["explored_count"]))

        row = connection.execute(load_sql("clear/count_all.sql")).fetchone()
        return ClearPlan(
            scope=clear_scope,
            explored=int(row["explored_count"]),
            offers=int(row["offers_count"]),
            rankings=int(row["rankings_count"]),
            ranking_runs=int(row["ranking_runs_count"]),
        )


def clear_data(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    scope: str,
) -> ClearPlan:
    plan = get_clear_plan(db_path=db_path, scope=scope)
    init_db(db_path)
    with _connect(db_path) as connection:
        if plan.scope == "rankings":
            connection.execute(load_sql("clear/delete_rankings.sql"))
        elif plan.scope == "offers":
            connection.execute(load_sql("clear/delete_offers.sql"))
        elif plan.scope == "explored":
            connection.execute(load_sql("clear/delete_explored.sql"))
        elif plan.scope == "all":
            connection.execute(load_sql("clear/delete_explored.sql"))
            connection.execute(load_sql("clear/delete_rankings.sql"))
            connection.execute(load_sql("clear/delete_offers.sql"))
            connection.execute(load_sql("clear/delete_ranking_runs.sql"))
        else:
            raise ValueError(f"Unsupported clear scope: {plan.scope}")
    return plan


def exclude_existing_offers(
    jobs: list[JobOffer],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[JobOffer]:
    if not jobs:
        return []

    init_db(db_path)
    source_id_pairs = sorted(
        {(job.source, job.source_id) for job in jobs if job.source_id is not None}
    )
    urls = sorted({str(job.url) for job in jobs})
    existing_source_ids: set[tuple[str, str]] = set()
    existing_urls: set[str] = set()

    with _connect(db_path) as connection:
        for start in range(0, len(source_id_pairs), 250):
            chunk = source_id_pairs[start:start + 250]
            placeholders = ", ".join(["(?, ?)"] * len(chunk))
            params = [value for pair in chunk for value in pair]
            rows = connection.execute(
                (
                    "SELECT source, source_id FROM offers "
                    f"WHERE (source, source_id) IN ({placeholders})"
                ),
                params,
            ).fetchall()
            existing_source_ids.update((row["source"], row["source_id"]) for row in rows)

        for start in range(0, len(urls), 500):
            chunk = urls[start:start + 500]
            placeholders = ", ".join(["?"] * len(chunk))
            rows = connection.execute(
                f"SELECT url FROM offers WHERE url IN ({placeholders})",
                chunk,
            ).fetchall()
            existing_urls.update(row["url"] for row in rows)

    new_jobs: list[JobOffer] = []
    seen_source_ids: set[tuple[str, str]] = set()
    seen_urls: set[str] = set()
    for job in jobs:
        source_id_key = (job.source, job.source_id) if job.source_id is not None else None
        url = str(job.url)
        if source_id_key is not None and source_id_key in existing_source_ids:
            continue
        if url in existing_urls:
            continue
        if source_id_key is not None and source_id_key in seen_source_ids:
            continue
        if url in seen_urls:
            continue
        if source_id_key is not None:
            seen_source_ids.add(source_id_key)
        seen_urls.add(url)
        new_jobs.append(job)

    return new_jobs


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


def _like_pattern(value: str | None) -> str | None:
    cleaned = (value or "").strip().lower()
    if not cleaned or not any(character.isalnum() for character in cleaned):
        return None
    escaped = (
        cleaned.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"%{escaped}%"


def list_ranked_offers(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    recommendation: str | None = None,
    status: str | None = None,
    source: str | None = None,
    location: str | None = None,
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
    location_pattern = _like_pattern(location)
    if location_pattern:
        clauses.append("LOWER(COALESCE(offers.location, '')) LIKE ? ESCAPE '\\'")
        params.append(location_pattern)
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


def list_unranked_review_offers(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    search: str | None = None,
    source: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    init_db(db_path)
    clauses: list[str] = []
    params: list[Any] = []
    search_pattern = _like_pattern(search)
    if search_pattern:
        clauses.append(
            "("
            "LOWER(offers.title) LIKE ? ESCAPE '\\' "
            "OR LOWER(offers.company) LIKE ? ESCAPE '\\' "
            "OR LOWER(offers.description) LIKE ? ESCAPE '\\'"
            ")"
        )
        params.extend([search_pattern, search_pattern, search_pattern])
    if source:
        clauses.append("offers.source = ?")
        params.append(source)

    filter_sql = f"AND {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with _connect(db_path) as connection:
        sql = load_sql("offers/select_unranked_review.sql").replace(
            "/*FILTER_CLAUSE*/",
            filter_sql,
        )
        rows = connection.execute(sql, params).fetchall()

    return [dict(row) for row in rows]


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
