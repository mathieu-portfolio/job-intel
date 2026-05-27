from __future__ import annotations

from app.storage._common import *  # noqa: F401,F403
from app.storage._offers_impl import _job_from_offer_row
from app.storage._reviews_impl import _main_signal_terms, _like_pattern
from app.filtering.rule_scoring import score_profile_match

def save_screening_result(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    offer_id: int,
    profile_id: str = "default",
    evaluation: RuleEvaluation,
    threshold: int,
    screened_at: str | None = None,
) -> int:
    init_db(db_path)
    screened_at = screened_at or _now_iso()
    with _connect(db_path) as connection:
        save_screening_results_batch(
            connection,
            [(offer_id, profile_id, evaluation, threshold)],
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
    profile_id: str = "default",
    preset_id: str,
    evaluation: RuleEvaluation,
    scored_at: str | None = None,
) -> None:
    # Compatibility wrapper: preset_id is ignored because rule matches are now
    # stored once per offer/profile and scored against presets at read time.
    init_db(db_path)
    with _connect(db_path) as connection:
        save_offer_scores_batch(connection, [(offer_id, profile_id, evaluation)], scored_at=scored_at)


def save_offer_scores(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    offer_id: int,
    profile_id: str,
    evaluations: dict[str, RuleEvaluation],
    scored_at: str | None = None,
) -> None:
    # Compatibility wrapper for older callers passing a preset->evaluation map.
    evaluation = next(iter(evaluations.values())) if evaluations else None
    if evaluation is None:
        return
    init_db(db_path)
    with _connect(db_path) as connection:
        save_offer_scores_batch(connection, [(offer_id, profile_id, evaluation)], scored_at=scored_at)


def save_offer_scores_batch(
    connection: sqlite3.Connection,
    rows: list[tuple[int, str, RuleEvaluation]] | list[tuple[int, str, str, RuleEvaluation]],
    *,
    scored_at: str | None = None,
) -> None:
    scored_at = scored_at or _now_iso()
    normalized_rows: list[tuple[int, str, RuleEvaluation]] = []
    for row in rows:
        if len(row) == 4:
            offer_id, profile_id, _preset_id, evaluation = row  # type: ignore[misc]
        else:
            offer_id, profile_id, evaluation = row  # type: ignore[misc]
        normalized_rows.append((offer_id, profile_id, evaluation))
    connection.executemany(
        """
        INSERT INTO offer_profile_matches (
            offer_id, profile_id, category_scores_json, signals_json, matched_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(offer_id, profile_id) DO UPDATE SET
            category_scores_json = excluded.category_scores_json,
            signals_json = excluded.signals_json,
            matched_at = excluded.matched_at;
        """,
        [
            (
                offer_id,
                profile_id,
                json.dumps(_category_scores_from_evaluation(evaluation), ensure_ascii=False),
                json.dumps(_signals_from_evaluation(evaluation), ensure_ascii=False),
                scored_at,
            )
            for offer_id, profile_id, evaluation in normalized_rows
        ],
    )


def _category_scores_from_evaluation(evaluation: RuleEvaluation) -> dict[str, float]:
    scores: dict[str, float] = {}
    for match in [*evaluation.matched_positive_terms, *evaluation.matched_negative_terms]:
        if match.category:
            scores[match.category] = scores.get(match.category, 0.0) + float(match.contribution or 0.0)
    return scores


def _evaluation_from_match_facts(
    category_scores_json: str | None,
    signals_json: str | None,
) -> RuleEvaluation:
    """Rebuild a preset-independent RuleEvaluation from stored match facts.

    Runtime scoring must be driven by category_scores_json. signals_json is
    only used to keep the original matched terms/reasoning available for UI
    explanations. When an old row has signals but no category scores, fall
    back to deriving category scores from the signals for migration safety.
    """
    category_scores: dict[str, float] = {}
    if category_scores_json:
        try:
            raw_scores = json.loads(category_scores_json)
        except json.JSONDecodeError:
            raw_scores = {}
        if isinstance(raw_scores, dict):
            for category, score in raw_scores.items():
                try:
                    category_scores[str(category)] = float(score)
                except (TypeError, ValueError):
                    continue

    payload: dict[str, Any] = {}
    if signals_json:
        try:
            raw_payload = json.loads(signals_json)
        except json.JSONDecodeError:
            raw_payload = {}
        if isinstance(raw_payload, dict):
            payload = raw_payload

    if category_scores:
        return RuleEvaluation.model_validate(
            {
                "score": 0,
                "normalized_score": 0,
                "matched_positive_terms": [
                    {
                        "term": f"category:{category}",
                        "weight": 0.0,
                        "category": category,
                        "contribution": score,
                    }
                    for category, score in category_scores.items()
                ],
                "matched_negative_terms": [],
                "reasoning": payload.get("reasoning", []),
                "decision": "skip",
            }
        )

    return RuleEvaluation.model_validate(
        {
            "score": 0,
            "normalized_score": 0,
            "matched_positive_terms": payload.get("positive", []),
            "matched_negative_terms": payload.get("negative", []),
            "reasoning": payload.get("reasoning", []),
            "decision": "skip",
        }
    )


def _evaluation_from_signals(signals_json: str | None) -> RuleEvaluation:
    # Backward-compatible private helper for any straggler callers. New runtime
    # scoring paths must call _evaluation_from_match_facts with category scores.
    return _evaluation_from_match_facts(None, signals_json)


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
                profile_id,
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
            for offer_id, profile_id, evaluation, threshold in rows
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
    profile_id: str,
) -> int | None:
    init_db(db_path)
    with _connect(db_path) as connection:
        row = connection.execute(
            load_sql("screening_results/select_id.sql"),
            (offer_id, profile_id),
        ).fetchone()
    return int(row["id"]) if row is not None else None


def select_screened_offers(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    provider: str | None,
    model: str | None,
    profile_id: str,
    preset_id: str = DEFAULT_SCORING_PRESET_ID,
    min_score: int | None = None,
    limit: int,
    only_recent_days: int | None = None,
) -> list[StoredOffer]:
    init_db(db_path)
    preset = get_scoring_preset(preset_id, db_path=db_path)
    clauses: list[str] = ["offer_profile_matches.profile_id = ?"]
    params: list[Any] = [profile_id]
    if only_recent_days is not None:
        cutoff = (datetime.now() - timedelta(days=only_recent_days)).isoformat(timespec="seconds")
        clauses.append("COALESCE(offers.published_at, offers.first_seen_at) >= ?")
        params.append(cutoff)
    where_sql = " AND ".join(clauses)
    with _connect(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT offers.*, offer_profile_matches.category_scores_json, offer_profile_matches.signals_json
            FROM offers
            JOIN offer_profile_matches ON offer_profile_matches.offer_id = offers.id
            WHERE {where_sql}
              AND NOT EXISTS (
                SELECT 1 FROM ai_reviews
                WHERE ai_reviews.offer_id = offers.id
                  AND ai_reviews.provider IS ?
                  AND ai_reviews.model IS ?
                  AND ai_reviews.profile_id = ?
                  AND ai_reviews.preset_id = ?
              );
            """,
            [*params, provider, model, profile_id, preset_id],
        ).fetchall()
    scored_rows: list[tuple[int, StoredOffer]] = []
    for row in rows:
        evaluation = score_profile_match(_evaluation_from_match_facts(row["category_scores_json"], row["signals_json"]), preset.weights)
        if min_score is not None and evaluation.normalized_score < min_score:
            continue
        scored_rows.append((evaluation.normalized_score, StoredOffer(id=row["id"], job=_job_from_offer_row(row))))
    scored_rows.sort(key=lambda item: item[0], reverse=True)
    return [offer for _score, offer in scored_rows[:limit]]


def list_screened_offers(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    profile_id: str | None = None,
    preset_id: str = DEFAULT_SCORING_PRESET_ID,
    threshold: int = 40,
    show_all_matching_presets: bool = False,
    search: str | None = None,
    source: str | None = None,
    sort: str = "score_desc",
    limit: int = 100,
) -> list[dict[str, Any]]:
    init_db(db_path)
    preset = get_scoring_preset(preset_id, db_path=db_path)
    presets_to_apply = (
        list_scoring_presets(db_path, enabled_only=True)
        if show_all_matching_presets
        else [preset]
    )
    clauses: list[str] = []
    params: list[Any] = []
    if profile_id:
        clauses.append("offer_profile_matches.profile_id = ?")
        params.append(profile_id)
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
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
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
                offer_profile_matches.profile_id,
                offer_profile_matches.category_scores_json,
                offer_profile_matches.signals_json,
                ai_reviews.score AS ai_score,
                ai_reviews.recommendation AS ai_verdict,
                ai_reviews.reviewed_at AS ai_reviewed_at
            FROM offers
            JOIN offer_profile_matches ON offer_profile_matches.offer_id = offers.id
            LEFT JOIN ai_reviews
              ON ai_reviews.offer_id = offers.id
             AND ai_reviews.preset_id = ?
             AND ai_reviews.profile_id = offer_profile_matches.profile_id
            {where_sql};
            """,
            [preset_id, *params],
        ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        profile_match = _evaluation_from_match_facts(row["category_scores_json"], row["signals_json"])
        for applied_preset in presets_to_apply:
            evaluation = score_profile_match(profile_match, applied_preset.weights)
            if evaluation.normalized_score < threshold:
                continue
            item = dict(row)
            item["preset_id"] = applied_preset.id
            item["preset_name"] = applied_preset.name
            item["fast_score"] = evaluation.normalized_score
            item["signals_json"] = row["signals_json"] or json.dumps(_signals_from_evaluation(evaluation), ensure_ascii=False)
            item["main_signals"] = _main_signal_terms(item["signals_json"])
            results.append(item)
    if sort == "offer_newest":
        results.sort(key=lambda item: item.get("published_at") or item.get("first_seen_at") or "", reverse=True)
    elif sort == "source":
        results.sort(key=lambda item: (item.get("source") or "", -item["fast_score"]))
    elif sort == "status":
        results.sort(key=lambda item: (item.get("review_status") or "", -item["fast_score"]))
    else:
        results.sort(key=lambda item: (item["fast_score"], item.get("last_fetched_at") or ""), reverse=True)
    return results[:limit]
