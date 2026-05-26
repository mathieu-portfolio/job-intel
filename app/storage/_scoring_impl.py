from __future__ import annotations

from app.storage._common import *  # noqa: F401,F403
from app.storage._offers_impl import _job_from_offer_row
from app.storage._reviews_impl import _main_signal_terms, _like_pattern

def save_screening_result(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    offer_id: int,
    profile_path: str,
    evaluation: RuleEvaluation,
    threshold: int,
    screened_at: str | None = None,
) -> int:
    init_db(db_path)
    screened_at = screened_at or _now_iso()
    with _connect(db_path) as connection:
        save_screening_results_batch(
            connection,
            [(offer_id, profile_path, evaluation, threshold)],
            screened_at=screened_at,
        )
        row = connection.execute(
            load_sql("screening_results/select_id.sql"),
            (offer_id, profile_path),
        ).fetchone()
    return int(row["id"])


def _signals_from_evaluation(evaluation: RuleEvaluation) -> dict[str, Any]:
    return {
        "positive": [match.model_dump(mode="json") for match in evaluation.matched_positive_terms],
        "negative": [match.model_dump(mode="json") for match in evaluation.matched_negative_terms],
        "reasoning": evaluation.reasoning,
    }


def save_offer_score(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    offer_id: int,
    preset_id: str,
    evaluation: RuleEvaluation,
    scored_at: str | None = None,
) -> None:
    init_db(db_path)
    with _connect(db_path) as connection:
        save_offer_scores_batch(connection, [(offer_id, preset_id, evaluation)], scored_at=scored_at)


def save_offer_scores(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    offer_id: int,
    evaluations: dict[str, RuleEvaluation],
    scored_at: str | None = None,
) -> None:
    init_db(db_path)
    with _connect(db_path) as connection:
        save_offer_scores_batch(
            connection,
            [(offer_id, preset_id, evaluation) for preset_id, evaluation in evaluations.items()],
            scored_at=scored_at,
        )


def save_offer_scores_batch(
    connection: sqlite3.Connection,
    rows: list[tuple[int, str, RuleEvaluation]],
    *,
    scored_at: str | None = None,
) -> None:
    scored_at = scored_at or _now_iso()
    connection.executemany(
        """
        INSERT INTO offer_scores (
            offer_id, preset_id, score, signals_json, scored_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(offer_id, preset_id) DO UPDATE SET
            score = excluded.score,
            signals_json = excluded.signals_json,
            scored_at = excluded.scored_at;
        """,
        [
            (
                offer_id,
                preset_id,
                evaluation.normalized_score,
                json.dumps(_signals_from_evaluation(evaluation), ensure_ascii=False),
                scored_at,
            )
            for offer_id, preset_id, evaluation in rows
        ],
    )


def save_screening_results_batch(
    connection: sqlite3.Connection,
    rows: list[tuple[int, str, RuleEvaluation, int]],
    *,
    screened_at: str | None = None,
) -> None:
    screened_at = screened_at or _now_iso()
    connection.executemany(
        load_sql("screening_results/upsert.sql"),
        [
            (
                offer_id,
                profile_path,
                evaluation.normalized_score,
                evaluation.decision,
                threshold,
                1 if evaluation.normalized_score >= threshold else 0,
                json.dumps(
                    {
                        "positive": [match.model_dump(mode="json") for match in evaluation.matched_positive_terms],
                        "negative": [match.model_dump(mode="json") for match in evaluation.matched_negative_terms],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(evaluation.reasoning, ensure_ascii=False),
                screened_at,
            )
            for offer_id, profile_path, evaluation, threshold in rows
        ],
    )


def list_scoring_presets(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    enabled_only: bool = False,
) -> list[ScoringPreset]:
    init_db(db_path)
    where_sql = "WHERE enabled = 1" if enabled_only else ""
    with _connect(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT id, name, description, weights_json, is_builtin, enabled
            FROM scoring_presets
            {where_sql}
            ORDER BY name ASC;
            """
        ).fetchall()

    presets: list[ScoringPreset] = []
    builtin_orders = {preset.id: preset.order for preset in BUILTIN_SCORING_PRESETS}
    for row in rows:
        try:
            weights = parse_rule_scoring_config(json.loads(row["weights_json"]))
        except (json.JSONDecodeError, RuntimeError, ValueError):
            weights = load_rule_scoring_config()
        presets.append(
            ScoringPreset(
                id=row["id"],
                name=row["name"],
                description=row["description"] or "",
                weights=weights,
                is_builtin=bool(row["is_builtin"]),
                enabled=bool(row["enabled"]),
                order=builtin_orders.get(row["id"], 100),
            )
        )
    return sorted(presets, key=lambda preset: (preset.order, preset.name.lower()))


def get_scoring_preset(
    preset_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> ScoringPreset:
    presets = list_scoring_presets(db_path, enabled_only=False)
    for preset in presets:
        if preset.id == preset_id:
            return preset
    for preset in presets:
        if preset.id == DEFAULT_SCORING_PRESET_ID:
            return preset
    raise ValueError("No scoring presets are configured.")


def find_screening_result_id(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    offer_id: int,
    profile_path: str,
) -> int | None:
    init_db(db_path)
    with _connect(db_path) as connection:
        row = connection.execute(
            load_sql("screening_results/select_id.sql"),
            (offer_id, profile_path),
        ).fetchone()
    return int(row["id"]) if row is not None else None


def select_screened_offers(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    provider: str | None,
    model: str | None,
    profile_path: str,
    preset_id: str = DEFAULT_SCORING_PRESET_ID,
    min_score: int | None = None,
    limit: int,
    only_recent_days: int | None = None,
) -> list[StoredOffer]:
    init_db(db_path)
    params: list[Any] = [preset_id]
    min_score_clause = ""
    if min_score is not None:
        min_score_clause = "  AND offer_scores.score >= ?\n"
        params.append(min_score)
    params.extend([provider, model, profile_path, preset_id])
    recent_clause = ""
    if only_recent_days is not None:
        cutoff = (datetime.now() - timedelta(days=only_recent_days)).isoformat(timespec="seconds")
        recent_clause = "AND COALESCE(offers.published_at, offers.first_seen_at) >= ?"
        params.append(cutoff)
    params.append(limit)

    sql = (
        load_sql("screening_results/select_screened_offers.sql")
        .replace("/*MIN_SCORE_FILTER*/", min_score_clause)
        .replace("/*RECENT_FILTER*/", recent_clause)
    )
    with _connect(db_path) as connection:
        rows = connection.execute(sql, params).fetchall()

    return [StoredOffer(id=row["id"], job=_job_from_offer_row(row)) for row in rows]


def list_screened_offers(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    preset_id: str = DEFAULT_SCORING_PRESET_ID,
    threshold: int = 40,
    show_all_matching_presets: bool = False,
    search: str | None = None,
    source: str | None = None,
    sort: str = "score_desc",
    limit: int = 100,
) -> list[dict[str, Any]]:
    init_db(db_path)
    clauses: list[str] = ["offer_scores.score >= ?"]
    where_params: list[Any] = [threshold]
    if not show_all_matching_presets:
        clauses.insert(0, "offer_scores.preset_id = ?")
        where_params.insert(0, preset_id)
    search_pattern = _like_pattern(search)
    if search_pattern:
        clauses.append(
            "("
            "LOWER(offers.title) LIKE ? ESCAPE '\\' "
            "OR LOWER(offers.company) LIKE ? ESCAPE '\\' "
            "OR LOWER(offers.description) LIKE ? ESCAPE '\\'"
            ")"
        )
        where_params.extend([search_pattern, search_pattern, search_pattern])
    if source:
        clauses.append("offers.source = ?")
        where_params.append(source)
    where_sql = " AND ".join(clauses)
    order_by = {
        "score_desc": "offer_scores.score DESC, offers.last_fetched_at DESC",
        "offer_newest": "COALESCE(offers.published_at, offers.first_seen_at) DESC, offer_scores.score DESC",
        "source": "offers.source ASC, offer_scores.score DESC",
        "status": "offers.review_status ASC, offer_scores.score DESC",
    }.get(sort, "offer_scores.score DESC, offers.last_fetched_at DESC")
    params = [preset_id, *where_params, limit]
    with _connect(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT
                offers.id AS offer_id,
                offers.source,
                offers.url,
                offers.title,
                offers.company,
                offers.location,
                offers.description,
                offers.published_at,
                offers.first_seen_at,
                offers.last_seen_at,
                offers.last_fetched_at,
                offers.review_status,
                offer_scores.preset_id,
                scoring_presets.name AS preset_name,
                offer_scores.score AS fast_score,
                offer_scores.signals_json,
                ai_reviews.score AS ai_score,
                ai_reviews.recommendation AS ai_verdict,
                ai_reviews.reviewed_at AS ai_reviewed_at
            FROM offers
            JOIN offer_scores ON offer_scores.offer_id = offers.id
            JOIN scoring_presets ON scoring_presets.id = offer_scores.preset_id
            LEFT JOIN ai_reviews
              ON ai_reviews.offer_id = offers.id
             AND ai_reviews.preset_id = ?
            WHERE {where_sql}
            ORDER BY {order_by}
            LIMIT ?;
            """,
            params,
        ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["main_signals"] = _main_signal_terms(row["signals_json"])
        results.append(item)
    return results
