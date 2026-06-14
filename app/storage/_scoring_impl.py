from __future__ import annotations

from app.storage._common import *  # noqa: F401,F403
from app.storage._offers_impl import _job_from_offer_row
from app.storage._reviews_impl import _main_signal_terms, _like_pattern
from app.ai.decision import DecisionWeights, blend_seniority
from app.filtering.presets import load_builtin_scoring_presets

SCREENED_DECISION_WEIGHTS = DecisionWeights(ai=0.0, seniority=0.30)

def save_screening_result(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    offer_id: int,
    profile_path: str,
    profile_id: str | None = None,
    evaluation: RuleEvaluation,
    threshold: int,
    screened_at: str | None = None,
) -> int:
    init_db(db_path)
    screened_at = screened_at or _now_iso()
    profile_id = profile_id or profile_id_from_path(profile_path)
    with _connect(db_path) as connection:
        save_offer_scores_batch(
            connection,
            [(offer_id, profile_id, DEFAULT_SCORING_PRESET_ID, evaluation)],
            scored_at=screened_at,
        )
        save_screening_results_batch(
            connection,
            [(offer_id, profile_id, profile_path, evaluation, threshold)],
            screened_at=screened_at,
        )
        row = connection.execute(
            load_sql("screening_results/select_id.sql"),
            (offer_id, profile_id),
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
    profile_id: str = "default",
    scored_at: str | None = None,
) -> None:
    init_db(db_path)
    with _connect(db_path) as connection:
        save_offer_scores_batch(connection, [(offer_id, profile_id, preset_id, evaluation)], scored_at=scored_at)


def save_offer_scores(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    offer_id: int,
    evaluations: dict[str, RuleEvaluation],
    profile_id: str = "default",
    scored_at: str | None = None,
) -> None:
    init_db(db_path)
    with _connect(db_path) as connection:
        save_offer_scores_batch(
            connection,
            [(offer_id, profile_id, preset_id, evaluation) for preset_id, evaluation in evaluations.items()],
            scored_at=scored_at,
        )


def save_offer_scores_batch(
    connection: sqlite3.Connection,
    rows: list[tuple[int, str, RuleEvaluation] | tuple[int, str, str, RuleEvaluation]],
    *,
    scored_at: str | None = None,
) -> None:
    scored_at = scored_at or _now_iso()
    connection.executemany(
        """
        INSERT INTO offer_scores (
            offer_id, profile_id, preset_id, score, signals_json, scored_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(offer_id, profile_id, preset_id) DO UPDATE SET
            score = excluded.score,
            signals_json = excluded.signals_json,
            scored_at = excluded.scored_at;
        """,
        [
            (
                row[0],
                row[1] if len(row) == 4 else "default",
                row[2] if len(row) == 4 else row[1],
                (row[3] if len(row) == 4 else row[2]).normalized_score,
                json.dumps(_signals_from_evaluation(row[3] if len(row) == 4 else row[2]), ensure_ascii=False),
                scored_at,
            )
            for row in rows
        ],
    )


def save_screening_results_batch(
    connection: sqlite3.Connection,
    rows: list[
        tuple[int, str, RuleEvaluation, int]
        | tuple[int, str, str, RuleEvaluation, int]
    ],
    *,
    screened_at: str | None = None,
) -> None:
    screened_at = screened_at or _now_iso()
    connection.executemany(
        load_sql("screening_results/upsert.sql"),
        [
            (
                row[0],
                row[1] if len(row) == 5 else profile_id_from_path(row[1]),
                row[2] if len(row) == 5 else row[1],
                (row[3] if len(row) == 5 else row[2]).normalized_score,
                (row[3] if len(row) == 5 else row[2]).decision,
                row[4] if len(row) == 5 else row[3],
                0 if (row[3] if len(row) == 5 else row[2]).decision == "skip" else 1,
                json.dumps(
                    {
                        "positive": [
                            match.model_dump(mode="json")
                            for match in (row[3] if len(row) == 5 else row[2]).matched_positive_terms
                        ],
                        "negative": [
                            match.model_dump(mode="json")
                            for match in (row[3] if len(row) == 5 else row[2]).matched_negative_terms
                        ],
                    },
                    ensure_ascii=False,
                ),
                json.dumps((row[3] if len(row) == 5 else row[2]).reasoning, ensure_ascii=False),
                json.dumps(
                    {
                        name: score.model_dump(mode="json") if hasattr(score, "model_dump") else score
                        for name, score in (row[3] if len(row) == 5 else row[2]).category_scores.items()
                    },
                    ensure_ascii=False,
                ),
                json.dumps((row[3] if len(row) == 5 else row[2]).seniority.model_dump(mode="json"), ensure_ascii=False),
                screened_at,
            )
            for row in rows
        ],
    )


def list_scoring_presets(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    enabled_only: bool = False,
) -> list[ScoringPreset]:
    """Return scoring presets from config/scoring_presets.

    Presets are configuration, not application data. The database may still
    contain legacy scoring_presets rows from older versions, but those rows are
    intentionally ignored so removing a JSON file immediately removes the
    preset from the UI and from runtime scoring.
    """
    del db_path  # Kept for API compatibility with callers.
    try:
        presets = list(load_builtin_scoring_presets())
    except RuntimeError as error:
        if str(error).startswith("No scoring presets found"):
            return []
        raise
    if enabled_only:
        presets = [preset for preset in presets if preset.enabled]
    return sorted(presets, key=lambda preset: (preset.order, preset.name.lower()))


def get_scoring_preset(
    preset_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> ScoringPreset:
    del db_path  # Kept for API compatibility with callers.
    presets = list_scoring_presets(enabled_only=False)
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
    profile_id: str | None = None,
) -> int | None:
    init_db(db_path)
    profile_id = profile_id or profile_id_from_path(profile_path)
    with _connect(db_path) as connection:
        row = connection.execute(
            load_sql("screening_results/select_id.sql"),
            (offer_id, profile_id),
        ).fetchone()
    return int(row["id"]) if row is not None else None


def _category_scores_from_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _score_from_category_scores(raw: str | None, preset: ScoringPreset) -> tuple[float, int]:
    return score_category_scores(_category_scores_from_json(raw), preset.weights)


def _json_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _seniority_from_json(raw: str | None) -> dict[str, Any]:
    value = _json_object(raw)
    score = value.get("score")
    try:
        value["score"] = max(0, min(100, int(score)))
    except (TypeError, ValueError):
        value["score"] = 70
    value.setdefault("target_seniority", "unknown")
    value.setdefault("offer_seniority", "unknown")
    value.setdefault("confidence", 0)
    value.setdefault("reasoning", [])
    return value


def _category_score_breakdown(raw: str | None, preset: ScoringPreset) -> dict[str, Any]:
    """Build the same weighted category breakdown used for rule scoring.

    The stored screening result keeps match ratios per profile category. The
    selected preset decides how much each category matters. Keeping this helper
    as the single display path prevents the UI from showing ratios from one
    source and contributions from another.
    """
    category_scores = _category_scores_from_json(raw)
    details: list[dict[str, Any]] = []
    raw_score = 0.0

    for name, raw_category_score in category_scores.items():
        if isinstance(raw_category_score, dict):
            ratio = float(raw_category_score.get("ratio", 0.0) or 0.0)
            matched = float(raw_category_score.get("matched_weight", 0.0) or 0.0)
            total = float(raw_category_score.get("total_weight", 0.0) or 0.0)
        else:
            ratio = 0.0
            matched = 0.0
            total = 0.0

        weight = float(preset.weights.category_weights.get(name, 0.0) or 0.0)
        contribution = ratio * weight
        raw_score += contribution

        if not contribution and not matched:
            continue
        details.append(
            {
                "name": name.replace("_", " "),
                "ratio": round(ratio * 100, 2),
                "matched_weight": matched,
                "total_weight": total,
                "preset_weight": round(weight, 4),
                "preset_weight_percent": round(weight * 100, 2),
                "contribution": round(contribution, 4),
                "contribution_percent": round(contribution * 100, 2),
                "is_weighted": weight != 0.0,
            }
        )

    return {
        "raw_score": round(raw_score, 4),
        "details": sorted(
            details,
            key=lambda item: (not item["is_weighted"], -abs(item["contribution"]), item["name"]),
        ),
        "preset_name": preset.name,
        "no_signal_score": preset.weights.no_signal_score,
        "positive_score_scale": preset.weights.positive_score_scale,
        "negative_score_scale": preset.weights.negative_score_scale,
    }


def _screened_score_details(row: sqlite3.Row, preset: ScoringPreset) -> dict[str, Any]:
    breakdown = _category_score_breakdown(row["category_scores_json"], preset)
    raw_score, rule_score = _score_from_category_scores(row["category_scores_json"], preset)
    seniority = _seniority_from_json(row["seniority_json"] if "seniority_json" in row.keys() else None)
    seniority_score = int(seniority["score"])
    final_score = blend_seniority(rule_score, seniority_score, SCREENED_DECISION_WEIGHTS.seniority)
    return {
        "rule_score": rule_score,
        "raw_category_score": round(float(breakdown["raw_score"]), 4),
        "seniority_score": seniority_score,
        "seniority": seniority,
        "final_score": final_score,
        "base_score": rule_score,
        "base_weight": int(round((1.0 - SCREENED_DECISION_WEIGHTS.seniority) * 100)),
        "seniority_weight": int(round(SCREENED_DECISION_WEIGHTS.seniority * 100)),
        "preset_name": breakdown["preset_name"],
        "no_signal_score": breakdown["no_signal_score"],
        "positive_score_scale": breakdown["positive_score_scale"],
        "negative_score_scale": breakdown["negative_score_scale"],
    }


def _category_score_details(raw: str | None, preset: ScoringPreset) -> list[dict[str, Any]]:
    return _category_score_breakdown(raw, preset)["details"]


def _signals_json_for_row(row: sqlite3.Row) -> str:
    return row["matched_signals_json"] or "{}"


def select_screened_offers(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    provider: str | None,
    model: str | None,
    profile_path: str,
    profile_id: str | None = None,
    preset_id: str = DEFAULT_SCORING_PRESET_ID,
    min_score: int | None = None,
    limit: int,
    only_recent_days: int | None = None,
    exclude_ai_reviewed: bool = True,
) -> list[StoredOffer]:
    init_db(db_path)
    profile_id = profile_id or profile_id_from_path(profile_path)
    preset = get_scoring_preset(preset_id, db_path=db_path)
    params: list[Any] = [profile_id]
    review_clause = ""
    if exclude_ai_reviewed:
        review_clause = """
              AND NOT EXISTS (
                  SELECT 1
                  FROM ai_reviews
                  WHERE ai_reviews.offer_id = offers.id
                    AND ai_reviews.provider IS ?
                    AND ai_reviews.model IS ?
                    AND ai_reviews.profile_id = ?
              )
        """
        params.extend([provider, model, profile_id])
    recent_clause = ""
    if only_recent_days is not None:
        cutoff = (datetime.now() - timedelta(days=only_recent_days)).isoformat(timespec="seconds")
        recent_clause = "AND COALESCE(offers.published_at, offers.first_seen_at) >= ?"
        params.append(cutoff)

    with _connect(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT
                offers.*,
                screening_results.category_scores_json,
                screening_results.seniority_json
            FROM offers
            JOIN screening_results ON screening_results.offer_id = offers.id
            WHERE screening_results.profile_id = ?
              AND screening_results.passed = 1
              {review_clause}
              {recent_clause}
            """,
            params,
        ).fetchall()

    sorted_rows = sorted(
        rows,
        key=lambda row: (
            _screened_score_details(row, preset)["final_score"],
            row["published_at"] or "",
            row["first_seen_at"] or "",
        ),
        reverse=True,
    )
    if min_score is not None:
        sorted_rows = [
            row for row in sorted_rows
            if _screened_score_details(row, preset)["final_score"] >= min_score
        ]
    return [StoredOffer(id=row["id"], job=_job_from_offer_row(row)) for row in sorted_rows[:limit]]


def list_screened_offers(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    preset_id: str = DEFAULT_SCORING_PRESET_ID,
    profile_id: str = "default",
    threshold: int | None = None,
    search: str | None = None,
    source: str | None = None,
    location: str | None = None,
    review_status: str | None = None,
    min_score: int | None = None,
    ready_for_ai_review: bool = False,
    sort: str = "score",
    reverse_order: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    init_db(db_path)
    preset = get_scoring_preset(preset_id, db_path=db_path)
    clauses: list[str] = ["screening_results.profile_id = ?", "screening_results.passed = 1"]
    where_params: list[Any] = [profile_id]
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
    location_pattern = _like_pattern(location)
    if location_pattern:
        clauses.append("LOWER(offers.location) LIKE ? ESCAPE '\\'")
        where_params.append(location_pattern)
    if review_status:
        clauses.append("offers.review_status = ?")
        where_params.append(review_status)
    if min_score is not None:
        clauses.append("screening_results.score >= ?")
        where_params.append(min_score)
    if ready_for_ai_review:
        clauses.append(
            "NOT EXISTS ("
            "SELECT 1 FROM rankings "
            "WHERE rankings.offer_id = offers.id "
            "AND rankings.profile_id = screening_results.profile_id"
            ")"
        )
    where_sql = " AND ".join(clauses)
    params = [preset_id, preset_id, preset.name, profile_id, *where_params]
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
                ? AS preset_id,
                ? AS selected_preset_id,
                ? AS preset_name,
                screening_results.score AS stored_score,
                screening_results.matched_signals_json,
                screening_results.category_scores_json,
                screening_results.seniority_json,
                screening_results.reasoning_json,
                ai_reviews.score AS ai_score,
                ai_reviews.recommendation AS ai_verdict,
                ai_reviews.reviewed_at AS ai_reviewed_at
            FROM offers
            JOIN screening_results ON screening_results.offer_id = offers.id
            LEFT JOIN ai_reviews
             ON ai_reviews.offer_id = offers.id
             AND ai_reviews.profile_id = ?
            WHERE {where_sql};
            """,
            params,
        ).fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        score_details = _screened_score_details(row, preset)
        item["fast_score"] = score_details["final_score"]
        item["rule_score"] = score_details["rule_score"]
        item["seniority_score"] = score_details["seniority_score"]
        item["score_details"] = score_details
        item["category_score_details"] = _category_score_details(row["category_scores_json"], preset)
        item["main_signals"] = _main_signal_terms(_signals_json_for_row(row))
        results.append(item)

    def sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        if sort in {"date", "offer_newest"}:
            return (item["published_at"] or item["first_seen_at"] or "", item["fast_score"])
        return (item["fast_score"], item["last_fetched_at"] or "")

    reverse = not reverse_order
    safe_offset = max(offset, 0)
    return sorted(results, key=sort_key, reverse=reverse)[safe_offset:safe_offset + limit]
