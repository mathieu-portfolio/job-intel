from __future__ import annotations

from app.storage._common import *  # noqa: F401,F403
from app.storage._exploration_impl import _canonical_url

def upsert_offers(
    jobs: list[JobOffer],
    *,
    db_path: Path = DEFAULT_DB_PATH,
    fetched_at: str | None = None,
) -> UpsertStats:
    init_db(db_path)
    fetched_at = fetched_at or _now_iso()
    with _connect(db_path) as connection:
        stats, _ = upsert_offers_batch(connection, jobs, fetched_at=fetched_at)
    return stats


def upsert_offers_batch(
    connection: sqlite3.Connection,
    jobs: list[JobOffer],
    *,
    fetched_at: str | None = None,
) -> tuple[UpsertStats, dict[str, int]]:
    fetched_at = fetched_at or _now_iso()
    inserted = 0
    updated = 0
    offer_ids_by_url: dict[str, int] = {}

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
            cursor = connection.execute(
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
            offer_ids_by_url[url] = int(cursor.lastrowid)
        else:
            updated += 1
            offer_ids_by_url[url] = int(existing["id"])
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

    return UpsertStats(fetched=len(jobs), inserted=inserted, updated=updated), offer_ids_by_url


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


def find_existing_offer_ids_batch(
    connection: sqlite3.Connection,
    jobs: list[JobOffer],
) -> dict[str, int]:
    if not jobs:
        return {}
    source = jobs[0].source
    external_ids = sorted({job.source_id for job in jobs if job.source_id})
    urls = sorted({_canonical_url(job) for job in jobs})
    clauses: list[str] = []
    params: list[Any] = []
    if external_ids:
        clauses.append(
            f"(source = ? AND source_id IS NOT NULL AND source_id IN ({', '.join(['?'] * len(external_ids))}))"
        )
        params.append(source)
        params.extend(external_ids)
    if urls:
        clauses.append(f"url IN ({', '.join(['?'] * len(urls))})")
        params.extend(urls)
    if not clauses:
        return {}

    rows = connection.execute(
        f"SELECT id, source, source_id, url FROM offers WHERE {' OR '.join(clauses)};",
        params,
    ).fetchall()
    by_external_id = {
        (row["source"], row["source_id"]): int(row["id"])
        for row in rows
        if row["source_id"] is not None
    }
    by_url = {row["url"]: int(row["id"]) for row in rows}
    return {
        _canonical_url(job): by_external_id.get((job.source, job.source_id), by_url.get(_canonical_url(job)))
        for job in jobs
        if by_external_id.get((job.source, job.source_id), by_url.get(_canonical_url(job))) is not None
    }


def find_existing_offer_ids_by_url_batch(
    connection: sqlite3.Connection,
    canonical_urls: list[str],
) -> dict[str, int]:
    urls = sorted({url for url in canonical_urls if url})
    if not urls:
        return {}
    rows = connection.execute(
        f"SELECT id, url FROM offers WHERE url IN ({', '.join(['?'] * len(urls))});",
        urls,
    ).fetchall()
    return {row["url"]: int(row["id"]) for row in rows}


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


def list_offer_locations(db_path: Path = DEFAULT_DB_PATH) -> list[str]:
    init_db(db_path)
    with _connect(db_path) as connection:
        rows = connection.execute(load_sql("offers/select_locations.sql")).fetchall()
    return [str(row["location"]) for row in rows]
