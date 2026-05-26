from __future__ import annotations

import re
import time
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

import requests

from app.ai.decision import make_final_decision
from app.ai.evaluator import build_job_evaluation_prompts, evaluate_job_with_ai
from app.filtering.rules import evaluate_job, load_rule_scoring_config, precompute_rule_matching
from app.llm.factory import ProviderName, create_llm_provider
from app.llm.ollama_client import OllamaLlmProvider
from app.models.evaluation import AiJobEvaluation, FinalDecision, RuleEvaluation
from app.models.job import JobOffer
from app.sources.adzuna import fetch_adzuna
from app.sources.arbeitnow import fetch_arbeitnow
from app.storage.files import load_profile
from app.storage.sqlite import (
    DEFAULT_DB_PATH,
    DEFAULT_EXPLORED_CAPACITY,
    DEFAULT_RANKED_CAPACITY,
    DEFAULT_UNRANKED_CAPACITY,
    PruneStats,
    StoredOffer,
    UpsertStats,
    create_ranking_run,
    find_screening_result_id,
    find_existing_offer_ids_batch,
    find_existing_offer_ids_by_url_batch,
    get_exploration_metadata,
    get_scoring_preset,
    has_explored_offers_batch,
    init_db,
    list_scoring_presets,
    open_connection,
    prune_storage,
    record_explored_jobs_batch,
    save_ai_review,
    save_exploration_metadata,
    save_offer_scores_batch,
    save_ranking,
    save_screening_results_batch,
    select_screened_offers,
    select_unranked_offers,
    upsert_offers_batch,
)


RankingMode = Literal["rules", "ai", "hybrid"]
FetchSource = Literal["arbeitnow", "adzuna"]
ExplorationMode = Literal["safe", "normal", "fast_backfill"]
RankedResult = tuple[StoredOffer, RuleEvaluation, AiJobEvaluation | None, FinalDecision]
ProgressCallback = Callable[[str], None]
CancellationCheck = Callable[[], bool]
FAST_BACKFILL_SKIP_PAGE_LIMIT = 5


class WorkflowCancelled(RuntimeError):
    pass


def _raise_if_cancelled(cancelled: CancellationCheck | None) -> None:
    if cancelled is not None and cancelled():
        raise WorkflowCancelled("Workflow cancelled.")


@dataclass(frozen=True)
class FetchWorkflowResult:
    source: FetchSource
    db_path: Path
    stats: UpsertStats
    prune_stats: PruneStats
    matched_count: int
    matches: list[tuple[JobOffer, RuleEvaluation]]
    messages: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RankWorkflowResult:
    profile_path: Path
    db_path: Path
    ranking_mode: RankingMode
    provider_name: str | None
    model_name: str | None
    selected_count: int
    prefiltered_count: int
    ai_evaluation_count: int
    skipped_count: int
    saved_count: int
    run_id: int | None
    ranked: list[RankedResult]
    candidates: list[tuple[StoredOffer, RuleEvaluation]]
    messages: list[str] = field(default_factory=list)


def _emit(messages: list[str], progress: ProgressCallback | None, message: str) -> None:
    messages.append(message)
    if progress is not None:
        progress(message)


def _safe_debug_name(index: int, title: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", title.lower()).strip("-")
    return f"{index:03d}-{cleaned[:60] or 'job'}"


def _dump_debug_prompts(
    *,
    index: int,
    job: JobOffer,
    system_prompt: str,
    user_prompt: str,
) -> None:
    debug_dir = Path("debug")
    debug_dir.mkdir(exist_ok=True)
    base_name = _safe_debug_name(index, job.title)
    (debug_dir / f"{base_name}.system.txt").write_text(system_prompt, encoding="utf-8")
    (debug_dir / f"{base_name}.user.json").write_text(user_prompt, encoding="utf-8")


def prompt_size(system_prompt: str, user_prompt: str) -> int:
    return len(system_prompt.encode("utf-8")) + len(user_prompt.encode("utf-8"))


def format_timeout(timeout_seconds: float | None) -> str:
    if timeout_seconds is None:
        return "provider default"
    return f"{timeout_seconds:g}s"


def ranking_result_payload(
    *,
    stored_offer: StoredOffer,
    rule_evaluation: RuleEvaluation,
    ai_evaluation: AiJobEvaluation | None,
    final_decision: FinalDecision,
) -> dict[str, object]:
    return {
        "offer_id": stored_offer.id,
        "job": stored_offer.job.model_dump(mode="json"),
        "rule_evaluation": rule_evaluation.model_dump(mode="json"),
        "raw_ai_evaluation": (
            ai_evaluation.model_dump(mode="json") if ai_evaluation is not None else None
        ),
        "final_decision": final_decision.model_dump(mode="json"),
    }


def _offer_exploration_id(job: JobOffer) -> str:
    return job.source_id or str(job.url)


def _exploration_scope_payload(
    *,
    source: FetchSource,
    query: str,
    country: str,
    where: str | None,
    profile_path: Path,
    min_score: int | None,
) -> dict[str, object]:
    return {
        "source": source,
        "query": query,
        "country": country,
        "where": where or "",
        "profile_path": str(profile_path),
        "min_score": min_score,
    }


def _exploration_scope_key(scope: dict[str, object]) -> str:
    payload = json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fetch_offers(
    *,
    source: FetchSource = "arbeitnow",
    page: int = 1,
    pages: int = 1,
    new_offers: int | None = None,
    max_pages: int | None = None,
    max_seen_pages: int = 5,
    query: str = "c++ simulation",
    country: str = "fr",
    where: str | None = None,
    profile_path: Path = Path("profiles/default.json"),
    db_path: Path = DEFAULT_DB_PATH,
    min_score: int | None = None,
    explored_capacity: int = DEFAULT_EXPLORED_CAPACITY,
    unranked_capacity: int = DEFAULT_UNRANKED_CAPACITY,
    ranked_capacity: int = DEFAULT_RANKED_CAPACITY,
    exploration_mode: ExplorationMode = "safe",
    progress: ProgressCallback | None = None,
    cancelled: CancellationCheck | None = None,
) -> FetchWorkflowResult:
    messages: list[str] = []
    timing = {
        "provider_fetch": 0.0,
        "explored_lookup": 0.0,
        "scoring": 0.0,
        "offer_upsert": 0.0,
        "score_persistence": 0.0,
        "screened_persistence": 0.0,
        "explored_persistence": 0.0,
    }
    total_started_at = time.perf_counter()
    if pages < 1:
        raise ValueError("pages must be at least 1.")
    if new_offers is not None and new_offers < 1:
        raise ValueError("new_offers must be at least 1.")
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be at least 1.")
    if max_seen_pages < 1:
        raise ValueError("max_seen_pages must be at least 1.")
    if exploration_mode not in {"safe", "normal", "fast_backfill"}:
        raise ValueError("exploration_mode must be safe, normal, or fast_backfill.")

    target_new_offers = new_offers
    page_limit = max_pages if target_new_offers is not None else pages
    if page_limit is None:
        page_limit = pages

    _emit(
        messages,
        progress,
        (
            f"Fetching jobs from {source}; "
            f"target {target_new_offers or 'page scan'} newly explored offers; max pages {page_limit}."
        ),
    )
    _raise_if_cancelled(cancelled)

    fetched = 0
    inserted = 0
    updated = 0
    already_seen = 0
    newly_explored = 0
    filtered_out = 0
    errors = 0
    pages_scanned = 0
    consecutive_seen_pages = 0
    matches: list[tuple[JobOffer, RuleEvaluation]] = []
    candidate_profile = load_profile(profile_path)
    screening_threshold = min_score if min_score is not None else candidate_profile.screening_threshold
    init_db(db_path)
    enabled_presets = list_scoring_presets(db_path, enabled_only=True)
    if not enabled_presets:
        raise RuntimeError("No enabled scoring presets are configured.")
    precompute_rule_matching(candidate_profile, [preset.weights for preset in enabled_presets])

    scope_payload = _exploration_scope_payload(
        source=source,
        query=query,
        country=country,
        where=where,
        profile_path=profile_path,
        min_score=min_score,
    )
    scope_key = _exploration_scope_key(scope_payload)
    metadata = get_exploration_metadata(db_path=db_path, scope_key=scope_key)
    previous_newest_id = metadata.newest_id if metadata else None
    previous_oldest_id = metadata.oldest_id if metadata else None
    previous_last_explored_page = metadata.last_explored_page if metadata else None
    fast_backfill_active = (
        exploration_mode == "fast_backfill"
        and bool(previous_newest_id)
        and bool(previous_oldest_id)
        and previous_last_explored_page is not None
        and previous_last_explored_page > 1
    )
    if exploration_mode == "fast_backfill" and not fast_backfill_active:
        _emit(messages, progress, "Fast backfill metadata missing or incomplete; using normal exploration.")
    elif fast_backfill_active:
        _emit(
            messages,
            progress,
            (
                "Fast backfill enabled; processing new top offers before "
                f"{previous_newest_id}, then jumping near page {previous_last_explored_page}."
            ),
        )

    first_seen_identity: str | None = None
    last_seen_identity: str | None = None
    last_scanned_page: int | None = None
    current_page = 1 if fast_backfill_active else page
    fast_phase = "top" if fast_backfill_active else "normal"
    skip_pages_scanned = 0

    def process_jobs_batch(jobs: list[JobOffer], *, page_counts: dict[str, int]) -> bool:
        nonlocal inserted
        nonlocal updated
        nonlocal already_seen
        nonlocal newly_explored
        nonlocal filtered_out
        nonlocal errors
        if not jobs:
            return False

        batch_started_at = time.perf_counter()
        with open_connection(db_path) as connection:
            lookup_started_at = time.perf_counter()
            explored_identities = has_explored_offers_batch(
                connection,
                jobs[0].source,
                [(job.source_id, str(job.url)) for job in jobs],
            )
            timing["explored_lookup"] += time.perf_counter() - lookup_started_at

            jobs_to_score: list[JobOffer] = []
            explored_records: list[tuple[JobOffer, str, str | None, bool]] = []
            stop_after_page = False
            for job in jobs:
                _raise_if_cancelled(cancelled)
                canonical_url = str(job.url)
                if (job.source_id, canonical_url) in explored_identities:
                    already_seen += 1
                    page_counts["already_seen"] += 1
                    continue

                newly_explored += 1
                page_counts["newly_explored"] += 1
                if target_new_offers is not None:
                    _emit(
                        messages,
                        progress,
                        f"Processed {newly_explored}/{target_new_offers} newly explored offers.",
                    )

                if not job.description.strip():
                    filtered_out += 1
                    explored_records.append((job, "filtered_out", "missing_description", False))
                else:
                    jobs_to_score.append(job)

                if target_new_offers is not None and newly_explored >= target_new_offers:
                    stop_after_page = True
                    break

            lookup_started_at = time.perf_counter()
            existing_offer_ids = find_existing_offer_ids_batch(connection, jobs_to_score)
            existing_url_ids = find_existing_offer_ids_by_url_batch(
                connection,
                [str(job.url) for job in jobs_to_score],
            )
            timing["explored_lookup"] += time.perf_counter() - lookup_started_at

            score_rows: list[tuple[int, str, RuleEvaluation]] = []
            screening_rows: list[tuple[int, str, RuleEvaluation, int]] = []
            upsert_jobs: list[JobOffer] = []
            evaluations_by_url: dict[str, dict[str, RuleEvaluation]] = {}
            selected_evaluation_by_url: dict[str, RuleEvaluation] = {}

            for job in jobs_to_score:
                canonical_url = str(job.url)
                try:
                    scoring_started_at = time.perf_counter()
                    preset_evaluations = {
                        preset.id: evaluate_job(job, profile=candidate_profile, config=preset.weights)
                        for preset in enabled_presets
                    }
                    timing["scoring"] += time.perf_counter() - scoring_started_at
                    evaluation = preset_evaluations.get("balanced") or next(iter(preset_evaluations.values()))
                    evaluations_by_url[canonical_url] = preset_evaluations
                    selected_evaluation_by_url[canonical_url] = evaluation

                    existing_offer_id = existing_offer_ids.get(canonical_url)
                    if existing_offer_id is not None:
                        score_rows.extend(
                            (existing_offer_id, preset_id, preset_evaluation)
                            for preset_id, preset_evaluation in preset_evaluations.items()
                        )
                        screening_rows.append(
                            (existing_offer_id, str(profile_path), evaluation, screening_threshold)
                        )
                        if canonical_url in existing_url_ids:
                            upsert_jobs.append(job)
                            explored_records.append((job, "updated", "already_in_offers", False))
                        else:
                            explored_records.append((job, "duplicate", "already_in_offers", False))
                        continue

                    best_score = max(score.normalized_score for score in preset_evaluations.values())
                    if best_score < screening_threshold:
                        filtered_out += 1
                        explored_records.append((job, "filtered_out", "rule_filter_failed", False))
                        continue

                    upsert_jobs.append(job)
                    explored_records.append((job, "inserted", None, False))
                except Exception as error:
                    errors += 1
                    explored_records.append((job, "error", str(error), False))

            upsert_started_at = time.perf_counter()
            upsert_stats, upserted_offer_ids = upsert_offers_batch(connection, upsert_jobs)
            timing["offer_upsert"] += time.perf_counter() - upsert_started_at
            inserted += upsert_stats.inserted
            updated += upsert_stats.updated

            for job in upsert_jobs:
                canonical_url = str(job.url)
                offer_id = upserted_offer_ids.get(canonical_url)
                if offer_id is None:
                    continue
                preset_evaluations = evaluations_by_url[canonical_url]
                evaluation = selected_evaluation_by_url[canonical_url]
                score_rows.extend(
                    (offer_id, preset_id, preset_evaluation)
                    for preset_id, preset_evaluation in preset_evaluations.items()
                )
                screening_rows.append((offer_id, str(profile_path), evaluation, screening_threshold))
                if canonical_url not in existing_offer_ids:
                    matches.append((job, evaluation))

            score_started_at = time.perf_counter()
            save_offer_scores_batch(connection, score_rows)
            timing["score_persistence"] += time.perf_counter() - score_started_at

            screened_started_at = time.perf_counter()
            save_screening_results_batch(connection, screening_rows)
            timing["screened_persistence"] += time.perf_counter() - screened_started_at

            explored_started_at = time.perf_counter()
            record_explored_jobs_batch(connection, explored_records)
            timing["explored_persistence"] += time.perf_counter() - explored_started_at

        _emit(
            messages,
            progress,
            (
                f"Processed page batch: {len(jobs)} provider rows in "
                f"{time.perf_counter() - batch_started_at:.2f}s."
            ),
        )
        return stop_after_page

    while pages_scanned < page_limit:
        _raise_if_cancelled(cancelled)
        try:
            provider_started_at = time.perf_counter()
            if source == "arbeitnow":
                jobs = fetch_arbeitnow(page=current_page)
            elif source == "adzuna":
                jobs = fetch_adzuna(query=query, country=country, where=where, page=current_page)
            else:
                raise ValueError(f"Unsupported source: {source}")
            timing["provider_fetch"] += time.perf_counter() - provider_started_at
        except requests.RequestException as error:
            raise RuntimeError(f"Network/API error: {error}") from error

        pages_scanned += 1
        last_scanned_page = current_page
        fetched += len(jobs)
        _emit(messages, progress, f"Scanned page {current_page}: {len(jobs)} offers.")
        if not jobs:
            _emit(messages, progress, "Provider returned no more offers.")
            break

        if first_seen_identity is None:
            first_seen_identity = _offer_exploration_id(jobs[0])
        last_seen_identity = _offer_exploration_id(jobs[-1])

        page_counts = {"already_seen": 0, "newly_explored": 0}
        jump_page: int | None = None
        jobs_to_process: list[JobOffer] = []
        for index, job in enumerate(jobs):
            _raise_if_cancelled(cancelled)
            identity = _offer_exploration_id(job)
            if fast_phase == "top" and identity == previous_newest_id:
                jump_page = max(1, int(previous_last_explored_page or 1) - 1)
                fast_phase = "skip"
                skip_pages_scanned = 0
                _emit(messages, progress, f"Reached previous newest offer; jumping to page {jump_page}.")
                break
            if fast_phase == "skip":
                if identity == previous_oldest_id:
                    fast_phase = "normal"
                    _emit(messages, progress, "Reached previous oldest offer; resuming normal deduplication.")
                    jobs_to_process.extend(jobs[index + 1:])
                    break
                continue
            jobs_to_process.append(job)

        process_jobs_batch(jobs_to_process, page_counts=page_counts)

        if fast_phase == "skip":
            skip_pages_scanned += 1
            if skip_pages_scanned >= FAST_BACKFILL_SKIP_PAGE_LIMIT:
                fast_phase = "normal"
                _emit(
                    messages,
                    progress,
                    "Previous oldest offer was not found quickly; resuming normal deduplication.",
                )

        if page_counts["already_seen"] == len(jobs) and page_counts["newly_explored"] == 0:
            consecutive_seen_pages += 1
            if consecutive_seen_pages >= max_seen_pages:
                _emit(
                    messages,
                    progress,
                    f"Stopped after {consecutive_seen_pages} consecutive pages with only already-seen offers.",
                )
                break
        else:
            consecutive_seen_pages = 0
        if target_new_offers is not None and newly_explored >= target_new_offers:
            _emit(messages, progress, f"Processed {newly_explored} newly explored offers.")
            break
        if jump_page is not None:
            current_page = jump_page
            continue
        current_page += 1

    stats = UpsertStats(
        fetched=fetched,
        inserted=inserted,
        updated=updated,
        skipped_existing=already_seen,
        pages_scanned=pages_scanned,
        explored=newly_explored,
        newly_explored=newly_explored,
        already_seen=already_seen,
        filtered_out=filtered_out,
        errors=errors,
    )
    _emit(
        messages,
        progress,
        (
            f"Pages {stats.pages_scanned}; provider rows {stats.fetched}; "
            f"newly explored {stats.newly_explored}; "
            f"already seen {stats.already_seen}; screened out {stats.filtered_out}; "
            f"screened {stats.inserted + stats.updated}; inserted {stats.inserted}; "
            f"updated {stats.updated}; errors {stats.errors}."
        ),
    )
    total_elapsed = max(time.perf_counter() - total_started_at, 0.001)
    _emit(
        messages,
        progress,
        (
            "Fetch timing: "
            f"provider fetch {timing['provider_fetch']:.2f}s; "
            f"explored lookup {timing['explored_lookup']:.2f}s; "
            f"scoring {timing['scoring']:.2f}s; "
            f"offer upsert {timing['offer_upsert']:.2f}s; "
            f"score persistence {timing['score_persistence']:.2f}s; "
            f"screened persistence {timing['screened_persistence']:.2f}s; "
            f"explored persistence {timing['explored_persistence']:.2f}s; "
            f"{stats.fetched / total_elapsed:.1f} provider offers/sec."
        ),
    )
    if first_seen_identity and last_seen_identity and last_scanned_page is not None:
        _raise_if_cancelled(cancelled)
        save_exploration_metadata(
            db_path=db_path,
            scope_key=scope_key,
            source=source,
            scope=scope_payload,
            newest_id=first_seen_identity,
            oldest_id=last_seen_identity,
            last_explored_page=last_scanned_page,
        )
    _raise_if_cancelled(cancelled)
    prune_stats = prune_storage(
        db_path,
        explored_capacity=explored_capacity,
        unranked_capacity=unranked_capacity,
        ranked_capacity=ranked_capacity,
    )
    if (
        prune_stats.deleted_explored
        or prune_stats.deleted_unranked
        or prune_stats.deleted_ranked
    ):
        _emit(
            messages,
            progress,
            (
                "Pruned storage: "
                f"explored {prune_stats.deleted_explored}; "
                f"unranked {prune_stats.deleted_unranked}; "
                f"ranked {prune_stats.deleted_ranked}."
            ),
        )
    return FetchWorkflowResult(
        source=source,
        db_path=db_path,
        stats=stats,
        prune_stats=prune_stats,
        matched_count=len(matches),
        matches=matches,
        messages=messages,
    )


def rank_offers(
    *,
    profile_path: Path = Path("profiles/default.json"),
    db_path: Path = DEFAULT_DB_PATH,
    limit: int = 10,
    only_recent_days: int | None = None,
    dry_run: bool = False,
    min_score: int = 40,
    weights_path: Path | None = None,
    ranking_mode: RankingMode = "hybrid",
    provider: ProviderName | None = None,
    model: str | None = None,
    preset_id: str = "balanced",
    debug_prompt: bool = False,
    progress: ProgressCallback | None = None,
    cancelled: CancellationCheck | None = None,
) -> RankWorkflowResult:
    messages: list[str] = []
    _emit(messages, progress, f"Loading profile {profile_path}.")
    _raise_if_cancelled(cancelled)
    candidate_profile = load_profile(profile_path)
    scoring_preset = get_scoring_preset(preset_id, db_path=db_path)
    rule_config = load_rule_scoring_config(weights_path) if weights_path else scoring_preset.weights

    provider_name: str | None = None
    model_name: str | None = None
    llm_provider = None
    if ranking_mode != "rules":
        llm_provider = create_llm_provider(provider, model=model or None)
        provider_name = llm_provider.name
        model_name = llm_provider.model_name

    selected_offers = select_screened_offers(
        db_path=db_path,
        provider=provider_name,
        model=model_name,
        profile_path=str(profile_path),
        preset_id=scoring_preset.id,
        min_score=min_score,
        limit=limit,
        only_recent_days=only_recent_days,
    )
    if not selected_offers:
        selected_offers = select_unranked_offers(
            db_path=db_path,
            algorithm=ranking_mode,
            model=model_name,
            profile_path=str(profile_path),
            limit=limit,
            only_recent_days=only_recent_days,
        )
    _emit(messages, progress, f"Selected {len(selected_offers)} screened offers.")
    _raise_if_cancelled(cancelled)

    evaluated_jobs: list[tuple[StoredOffer, RuleEvaluation]] = []
    for stored_offer in selected_offers:
        _raise_if_cancelled(cancelled)
        evaluated_jobs.append(
            (stored_offer, evaluate_job(stored_offer.job, profile=candidate_profile, config=rule_config))
        )
    if ranking_mode == "hybrid":
        candidates = [
            (stored_offer, evaluation)
            for stored_offer, evaluation in evaluated_jobs
            if evaluation.normalized_score >= min_score
        ]
        prefiltered_count = len(candidates)
        skipped_count = len(evaluated_jobs) - prefiltered_count
        ai_evaluation_count = prefiltered_count
    else:
        candidates = evaluated_jobs
        prefiltered_count = len(candidates) if ranking_mode == "rules" else 0
        skipped_count = 0
        ai_evaluation_count = len(candidates) if ranking_mode == "ai" else 0

    _emit(
        messages,
        progress,
        (
            f"Prefiltered {prefiltered_count}; AI-evaluated {ai_evaluation_count}; "
            f"skipped {skipped_count}."
        ),
    )

    if not candidates or dry_run:
        return RankWorkflowResult(
            profile_path=profile_path,
            db_path=db_path,
            ranking_mode=ranking_mode,
            provider_name=provider_name,
            model_name=model_name,
            selected_count=len(selected_offers),
            prefiltered_count=prefiltered_count,
            ai_evaluation_count=ai_evaluation_count,
            skipped_count=skipped_count,
            saved_count=0,
            run_id=None,
            ranked=[],
            candidates=candidates,
            messages=messages,
        )

    ranked: list[RankedResult] = []
    run_timestamp = datetime.now()
    config_payload = {
        "ranking_mode": ranking_mode,
        "min_score": min_score,
        "limit": limit,
        "only_recent_days": only_recent_days,
        "weights_path": str(weights_path) if weights_path else None,
        "rule_config": rule_config.model_dump(mode="json"),
        "preset_id": scoring_preset.id,
        "preset_name": scoring_preset.name,
    }
    run_id = create_ranking_run(
        db_path=db_path,
        started_at=run_timestamp.isoformat(timespec="seconds"),
        algorithm=ranking_mode,
        model=model_name,
        profile_path=str(profile_path),
        config=config_payload,
    )

    if ranking_mode == "rules":
        for stored_offer, rule_evaluation in candidates:
            _raise_if_cancelled(cancelled)
            final_decision = make_final_decision(rule_evaluation=rule_evaluation)
            result_payload = ranking_result_payload(
                stored_offer=stored_offer,
                rule_evaluation=rule_evaluation,
                ai_evaluation=None,
                final_decision=final_decision,
            )
            save_ranking(
                db_path=db_path,
                run_id=run_id,
                offer_id=stored_offer.id,
                algorithm=ranking_mode,
                model=model_name,
                profile_path=str(profile_path),
                score=final_decision.final_score,
                recommendation=final_decision.recommendation,
                summary="Rule-only ranking.",
                result=result_payload,
            )
            ranked.append((stored_offer, rule_evaluation, None, final_decision))
    else:
        if llm_provider is None:
            raise RuntimeError("LLM provider was not initialized.")
        _emit(
            messages,
            progress,
            (
                f"Using {llm_provider.name} / {llm_provider.model_name} "
                f"with timeout {format_timeout(llm_provider.timeout_seconds)}."
            ),
        )
        if isinstance(llm_provider, OllamaLlmProvider):
            _emit(messages, progress, "Checking Ollama health and model.")
            _raise_if_cancelled(cancelled)
            llm_provider.check_ready()

        for index, (stored_offer, rule_evaluation) in enumerate(candidates, start=1):
            _raise_if_cancelled(cancelled)
            job = stored_offer.job
            _emit(messages, progress, f"Evaluating {index}/{len(candidates)}: {job.title}")
            system_prompt, user_prompt = build_job_evaluation_prompts(job, candidate_profile)
            if debug_prompt:
                _dump_debug_prompts(
                    index=index,
                    job=job,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
            started_at = time.perf_counter()
            ai_evaluation = evaluate_job_with_ai(
                job,
                candidate_profile,
                llm_provider,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            _raise_if_cancelled(cancelled)
            elapsed = time.perf_counter() - started_at
            _emit(messages, progress, f"Model response parsed in {elapsed:.1f}s.")
            final_decision = make_final_decision(
                rule_evaluation=rule_evaluation,
                ai_evaluation=ai_evaluation,
            )
            result_payload = ranking_result_payload(
                stored_offer=stored_offer,
                rule_evaluation=rule_evaluation,
                ai_evaluation=ai_evaluation,
                final_decision=final_decision,
            )
            save_ranking(
                db_path=db_path,
                run_id=run_id,
                offer_id=stored_offer.id,
                algorithm=ranking_mode,
                model=model_name,
                profile_path=str(profile_path),
                score=final_decision.final_score,
                recommendation=final_decision.recommendation,
                summary=ai_evaluation.summary,
                result=result_payload,
            )
            save_ai_review(
                db_path=db_path,
                screening_result_id=find_screening_result_id(
                    db_path=db_path,
                    offer_id=stored_offer.id,
                    profile_path=str(profile_path),
                ),
                offer_id=stored_offer.id,
                provider=provider_name,
                model=model_name,
                profile_path=str(profile_path),
                score=final_decision.final_score,
                recommendation=final_decision.recommendation,
                summary=ai_evaluation.summary,
                result=result_payload,
            )
            ranked.append((stored_offer, rule_evaluation, ai_evaluation, final_decision))

    ranked.sort(key=lambda item: item[3].final_score, reverse=True)
    _emit(messages, progress, f"Saved {len(ranked)} rankings.")
    return RankWorkflowResult(
        profile_path=profile_path,
        db_path=db_path,
        ranking_mode=ranking_mode,
        provider_name=provider_name,
        model_name=model_name,
        selected_count=len(selected_offers),
        prefiltered_count=prefiltered_count,
        ai_evaluation_count=ai_evaluation_count,
        skipped_count=skipped_count,
        saved_count=len(ranked),
        run_id=run_id,
        ranked=ranked,
        candidates=candidates,
        messages=messages,
    )
