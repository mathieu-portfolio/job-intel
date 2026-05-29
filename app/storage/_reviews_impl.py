from __future__ import annotations

from app.storage._common import *  # noqa: F401,F403
from app.ai.decision import DecisionWeights, blend_seniority, weighted_base_score
from app.filtering.rule_scoring import score_category_scores
from app.models.evaluation import recommendation_from_score

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
            (offer_id, provider, model, profile_id),
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


def list_ai_reviews_for_offers(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    offer_ids: list[int],
    provider: str | None,
    model: str | None,
    profile_id: str,
) -> dict[int, dict[str, Any]]:
    """Return existing pure AI reviews keyed by offer id.

    AI reviews are intentionally independent of scoring presets. A saved review
    answers "what does the AI think of this offer for this profile?", and the
    active preset is applied later when computing the final score.
    """
    if not offer_ids:
        return {}
    init_db(db_path)
    placeholders = ",".join("?" for _ in offer_ids)
    params: list[Any] = [provider, model, profile_id, *offer_ids]
    with _connect(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM ai_reviews
            WHERE provider IS ?
              AND model IS ?
              AND profile_id = ?
              AND offer_id IN ({placeholders})
            """,
            params,
        ).fetchall()

    reviews: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        try:
            item["review"] = json.loads(row["review_json"])
        except json.JSONDecodeError:
            item["review"] = {}
        reviews[int(row["offer_id"])] = item
    return reviews



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



def _review_ai_score(result: dict[str, Any]) -> int | None:
    raw_ai = result.get("raw_ai_evaluation")
    if isinstance(raw_ai, dict):
        try:
            return max(0, min(100, int(raw_ai.get("fit_score"))))
        except (TypeError, ValueError):
            pass
    final = result.get("final_decision")
    if isinstance(final, dict):
        try:
            value = final.get("ai_component")
            return None if value is None else max(0, min(100, int(value)))
        except (TypeError, ValueError):
            pass
    return None


def _review_seniority_score(rule: dict[str, Any]) -> int:
    seniority = rule.get("seniority")
    if isinstance(seniority, dict):
        try:
            return max(0, min(100, int(seniority.get("score"))))
        except (TypeError, ValueError):
            pass
    return 70


def _review_rule_score_for_preset(rule: dict[str, Any], preset: Any) -> int:
    category_scores = rule.get("category_scores")
    if isinstance(category_scores, dict) and category_scores:
        return score_category_scores(category_scores, preset.weights)[1]
    try:
        return max(0, min(100, int(rule.get("normalized_score"))))
    except (TypeError, ValueError):
        return 0


def _apply_review_display_preset(item: dict[str, Any], preset: Any) -> None:
    """Recompute displayed AI-reviewed score for the selected preset.

    AI reviews are stored independently of presets. The active preset should
    only change the deterministic rule component and the resulting final score
    shown in the AI reviewed list.
    """
    result = item.get("result")
    if not isinstance(result, dict):
        return
    rule = result.get("rule_evaluation")
    if not isinstance(rule, dict):
        return

    rule_score = _review_rule_score_for_preset(rule, preset)
    ai_score = _review_ai_score(result)
    seniority_score = _review_seniority_score(rule)
    weights = DecisionWeights()
    base_score = weighted_base_score(rule_score, ai_score, weights)
    final_score = blend_seniority(base_score, seniority_score, weights.seniority)
    recommendation = recommendation_from_score(final_score)

    rule["normalized_score"] = rule_score
    rule["score"] = rule_score
    final = result.get("final_decision")
    if not isinstance(final, dict):
        final = {}
        result["final_decision"] = final
    final.update(
        {
            "final_score": final_score,
            "recommendation": recommendation,
            "rule_component": rule_score,
            "ai_component": ai_score,
            "base_component": base_score,
            "seniority_component": seniority_score,
            "penalty_component": 0,
            "seniority_mismatch_penalty": max(0, 100 - seniority_score),
            "policy_adjustments": [],
            "reasoning": [
                (
                    f"Base score {base_score}/100 from rule component "
                    f"{rule_score}/100 and AI semantic component {ai_score}/100."
                    if ai_score is not None
                    else f"Base score {base_score}/100 from calibrated rule score {rule_score}/100."
                ),
                (
                    "Final score blends base score with deterministic seniority "
                    f"{seniority_score}/100 using seniority weight {weights.seniority:.0%}."
                ),
            ],
        }
    )
    item["score"] = final_score
    item["recommendation"] = recommendation
    item["selected_preset_id"] = getattr(preset, "id", "")
    item["selected_preset_name"] = getattr(preset, "name", "Selected preset")

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
    preset: Any | None = None,
    only_recent_days: int | None = None,
    ai_only: bool = False,
    sort: str = "score",
    reverse_order: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    init_db(db_path)
    clauses: list[str] = []
    params: list[Any] = []
    # Recommendation depends on the active preset because final scores are
    # recomputed at display time. Filter it after recomputing below.
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
        "score": "rankings.score DESC, rankings.ranked_at DESC",
        "score_desc": "rankings.score DESC, rankings.ranked_at DESC",
        "date": "rankings.ranked_at DESC, rankings.score DESC",
        "ranked_newest": "rankings.ranked_at DESC, rankings.score DESC",
        "offer_newest": "COALESCE(offers.published_at, offers.first_seen_at) DESC, rankings.score DESC",
    }.get(sort, "rankings.score DESC, rankings.ranked_at DESC")

    safe_offset = max(offset, 0)
    fetch_limit = max((safe_offset + limit) * 10, 1000)
    params.append(fetch_limit)
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
        item = {**dict(row), "result": result_json}
        if preset is not None:
            _apply_review_display_preset(item, preset)
        if recommendation and item.get("recommendation") != recommendation:
            continue
        results.append(item)

    def display_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        if sort in {"date", "ranked_newest"}:
            return (item.get("ranked_at") or "", item.get("score") or 0)
        if sort == "offer_newest":
            return (item.get("published_at") or item.get("first_seen_at") or "", item.get("score") or 0)
        return (item.get("score") or 0, item.get("ranked_at") or "")

    reverse = not reverse_order
    return sorted(results, key=display_sort_key, reverse=reverse)[safe_offset:safe_offset + limit]


def list_unranked_review_offers(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    search: str | None = None,
    source: str | None = None,
    profile_id: str = "default",
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    init_db(db_path)
    clauses: list[str] = [
        "EXISTS (SELECT 1 FROM screening_results WHERE screening_results.offer_id = offers.id AND screening_results.profile_id = ? AND screening_results.passed = 1)",
        "NOT EXISTS (SELECT 1 FROM rankings WHERE rankings.offer_id = offers.id AND rankings.profile_id = ?)",
    ]
    params: list[Any] = [profile_id, profile_id]
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

    where_sql = f"WHERE {' AND '.join(clauses)}"
    params.append(limit)
    params.append(max(offset, 0))
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
                offers.last_fetched_at
            FROM offers
            {where_sql}
            ORDER BY
                offers.last_fetched_at DESC,
                CASE WHEN offers.published_at IS NULL THEN 1 ELSE 0 END,
                offers.published_at DESC,
                offers.first_seen_at DESC
            LIMIT ? OFFSET ?;
            """,
            params,
        ).fetchall()

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
