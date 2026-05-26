from __future__ import annotations

from app.storage._common import *  # noqa: F401,F403

def create_ranking_run(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    started_at: str,
    algorithm: str,
    model: str | None,
    profile_path: str,
    config: dict[str, Any],
    profile_id: str | None = None,
) -> int:
    init_db(db_path)
    profile_id = profile_id or profile_id_from_path(profile_path)
    with _connect(db_path) as connection:
        cursor = connection.execute(
            load_sql("ranking_runs/insert.sql"),
            (
                started_at,
                algorithm,
                model,
                profile_id,
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
    profile_id: str | None = None,
    ranked_at: str | None = None,
) -> None:
    init_db(db_path)
    ranked_at = ranked_at or _now_iso()
    profile_id = profile_id or profile_id_from_path(profile_path)
    with _connect(db_path) as connection:
        connection.execute(
            load_sql("rankings/delete_existing.sql"),
            (offer_id, algorithm, model, profile_id),
        )
        connection.execute(
            load_sql("rankings/insert.sql"),
            (
                run_id,
                offer_id,
                algorithm,
                model,
                profile_id,
                profile_path,
                score,
                recommendation,
                summary,
                json.dumps(result, ensure_ascii=False),
                ranked_at,
            ),
        )


def _main_signal_terms(signals_json: str | None, *, limit: int = 4) -> list[str]:
    if not signals_json:
        return []
    try:
        signals = json.loads(signals_json)
    except json.JSONDecodeError:
        return []
    terms: list[str] = []
    for key in ("positive", "negative"):
        for signal in signals.get(key, []):
            term = signal.get("term") if isinstance(signal, dict) else None
            if term and term not in terms:
                terms.append(str(term))
            if len(terms) >= limit:
                return terms
    return terms


def save_ai_review(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    screening_result_id: int | None,
    offer_id: int,
    provider: str | None,
    model: str | None,
    profile_path: str,
    score: int,
    recommendation: Recommendation,
    summary: str,
    result: dict[str, Any],
    profile_id: str | None = None,
    preset_id: str = DEFAULT_SCORING_PRESET_ID,
    reviewed_at: str | None = None,
) -> None:
    init_db(db_path)
    reviewed_at = reviewed_at or _now_iso()
    profile_id = profile_id or profile_id_from_path(profile_path)
    with _connect(db_path) as connection:
        connection.execute(
            load_sql("ai_reviews/delete_existing.sql"),
            (offer_id, provider, model, profile_id, preset_id),
        )
        connection.execute(
            load_sql("ai_reviews/insert.sql"),
            (
                screening_result_id,
                offer_id,
                provider,
                model,
                profile_id,
                profile_path,
                preset_id,
                score,
                recommendation,
                summary,
                json.dumps(result, ensure_ascii=False),
                reviewed_at,
            ),
        )


def list_screening_results(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path)
    with _connect(db_path) as connection:
        rows = connection.execute(load_sql("screening_results/select_all_review.sql")).fetchall()
    return [dict(row) for row in rows]


def list_ai_reviews(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path)
    with _connect(db_path) as connection:
        rows = connection.execute(load_sql("ai_reviews/select_all_review.sql")).fetchall()
    return [dict(row) for row in rows]


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
    profile_id: str | None = None,
    profile_path: str | None = None,
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
    if profile_id:
        clauses.append("rankings.profile_id = ?")
        params.append(profile_id)
    elif profile_path:
        clauses.append("rankings.profile_id = ?")
        params.append(profile_id_from_path(profile_path))
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
