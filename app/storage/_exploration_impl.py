from __future__ import annotations

from app.storage._common import *  # noqa: F401,F403

def _canonical_url(job: JobOffer) -> str:
    return str(job.url)


def has_explored_offer(
    *,
    provider: str,
    external_id: str | None,
    canonical_url: str | None,
    profile_id: str = "default",
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    init_db(db_path)
    with _connect(db_path) as connection:
        row = connection.execute(
            load_sql("explored_offers/select_by_identity.sql"),
            (provider, profile_id, external_id, canonical_url, external_id),
        ).fetchone()
    return row is not None


def has_explored_offers_batch(
    connection: sqlite3.Connection,
    provider: str,
    identities: list[tuple[str | None, str]],
    *,
    profile_id: str = "default",
) -> set[tuple[str | None, str]]:
    if not identities:
        return set()
    external_ids = sorted({external_id for external_id, _ in identities if external_id})
    urls = sorted({canonical_url for _, canonical_url in identities if canonical_url})
    clauses: list[str] = []
    params: list[Any] = [provider, profile_id]
    if external_ids:
        clauses.append(f"external_id IN ({', '.join(['?'] * len(external_ids))})")
        params.extend(external_ids)
    if urls:
        clauses.append(f"canonical_url IN ({', '.join(['?'] * len(urls))})")
        params.extend(urls)
    if not clauses:
        return set()
    rows = connection.execute(
        f"""
        SELECT external_id, canonical_url
        FROM explored_offers
        WHERE provider = ?
          AND profile_id = ?
          AND ({' OR '.join(clauses)});
        """,
        params,
    ).fetchall()
    seen_external_ids = {row["external_id"] for row in rows if row["external_id"]}
    seen_urls = {row["canonical_url"] for row in rows if row["canonical_url"]}
    return {
        (external_id, canonical_url)
        for external_id, canonical_url in identities
        if (external_id and external_id in seen_external_ids) or canonical_url in seen_urls
    }


def record_explored_offer(
    *,
    provider: str,
    external_id: str | None,
    canonical_url: str | None,
    status: str,
    reason: str | None = None,
    keep_flag: bool = False,
    profile_id: str = "default",
    profile_path: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    seen_at: str | None = None,
) -> None:
    init_db(db_path)
    seen_at = seen_at or _now_iso()
    with _connect(db_path) as connection:
        row = connection.execute(
            load_sql("explored_offers/select_by_identity.sql"),
            (provider, profile_id, external_id, canonical_url, external_id),
        ).fetchone()
        if row is None:
            connection.execute(
                load_sql("explored_offers/insert.sql"),
                (
                    provider,
                    external_id,
                    canonical_url,
                    profile_id,
                    profile_path,
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
                (external_id, canonical_url, profile_path, seen_at, status, reason, 1 if keep_flag else 0, row["id"]),
            )


def record_explored_job(
    job: JobOffer,
    *,
    status: str,
    reason: str | None = None,
    keep_flag: bool = False,
    profile_id: str = "default",
    profile_path: str | None = None,
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
        profile_id=profile_id,
        profile_path=profile_path,
        db_path=db_path,
        seen_at=seen_at,
    )


def record_explored_jobs_batch(
    connection: sqlite3.Connection,
    records: list[tuple[JobOffer, str, str | None, bool]],
    *,
    profile_id: str = "default",
    profile_path: str | None = None,
    seen_at: str | None = None,
) -> None:
    seen_at = seen_at or _now_iso()
    for job, status, reason, keep_flag in records:
        canonical_url = _canonical_url(job)
        row = connection.execute(
            load_sql("explored_offers/select_by_identity.sql"),
            (job.source, profile_id, job.source_id, canonical_url, job.source_id),
        ).fetchone()
        if row is None:
            connection.execute(
                load_sql("explored_offers/insert.sql"),
                (
                    job.source,
                    job.source_id,
                    canonical_url,
                    profile_id,
                    profile_path,
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
                (job.source_id, canonical_url, profile_path, seen_at, status, reason, 1 if keep_flag else 0, row["id"]),
            )


def get_exploration_metadata(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    scope_key: str,
) -> ExplorationMetadata | None:
    init_db(db_path)
    with _connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT scope_key, newest_id, oldest_id, last_explored_page, updated_at
            FROM exploration_scopes
            WHERE scope_key = ?;
            """,
            (scope_key,),
        ).fetchone()
    if row is None:
        return None
    return ExplorationMetadata(
        scope_key=row["scope_key"],
        newest_id=row["newest_id"],
        oldest_id=row["oldest_id"],
        last_explored_page=(
            int(row["last_explored_page"]) if row["last_explored_page"] is not None else None
        ),
        updated_at=row["updated_at"],
    )


def save_exploration_metadata(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    scope_key: str,
    source: str,
    scope: dict[str, Any],
    newest_id: str | None,
    oldest_id: str | None,
    last_explored_page: int | None,
    updated_at: str | None = None,
) -> None:
    init_db(db_path)
    updated_at = updated_at or _now_iso()
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO exploration_scopes (
                scope_key, source, scope_json, newest_id, oldest_id, last_explored_page, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope_key) DO UPDATE SET
                source = excluded.source,
                scope_json = excluded.scope_json,
                newest_id = excluded.newest_id,
                oldest_id = excluded.oldest_id,
                last_explored_page = excluded.last_explored_page,
                updated_at = excluded.updated_at;
            """,
            (
                scope_key,
                source,
                json.dumps(scope, sort_keys=True, ensure_ascii=False),
                newest_id,
                oldest_id,
                last_explored_page,
                updated_at,
            ),
        )


def list_explored_offers(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path)
    with _connect(db_path) as connection:
        rows = connection.execute(load_sql("explored_offers/select_all.sql")).fetchall()
    return [dict(row) for row in rows]
