from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

import requests

from app.ai.decision import make_final_decision
from app.ai.evaluator import build_job_evaluation_prompts, evaluate_job_with_ai
from app.filtering.rules import evaluate_job, load_rule_scoring_config
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
    find_existing_offer_id,
    find_existing_offer_id_by_url,
    find_screening_result_id,
    has_explored_offer,
    prune_storage,
    record_explored_job,
    save_ai_review,
    save_ranking,
    save_screening_result,
    select_screened_offers,
    select_unranked_offers,
    upsert_offers,
)


RankingMode = Literal["rules", "ai", "hybrid"]
FetchSource = Literal["arbeitnow", "adzuna"]
RankedResult = tuple[StoredOffer, RuleEvaluation, AiJobEvaluation | None, FinalDecision]
ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class FetchWorkflowResult:
    source: FetchSource
    db_path: Path
    stats: UpsertStats
    prune_stats: PruneStats
    matched_count: int
    matches: list[tuple[JobOffer, RuleEvaluation]]


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
    progress: ProgressCallback | None = None,
) -> FetchWorkflowResult:
    messages: list[str] = []
    if pages < 1:
        raise ValueError("pages must be at least 1.")
    if new_offers is not None and new_offers < 1:
        raise ValueError("new_offers must be at least 1.")
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be at least 1.")
    if max_seen_pages < 1:
        raise ValueError("max_seen_pages must be at least 1.")

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
    rule_config = load_rule_scoring_config()

    for offset in range(page_limit):
        current_page = page + offset
        try:
            if source == "arbeitnow":
                jobs = fetch_arbeitnow(page=current_page)
            elif source == "adzuna":
                jobs = fetch_adzuna(query=query, country=country, where=where, page=current_page)
            else:
                raise ValueError(f"Unsupported source: {source}")
        except requests.RequestException as error:
            raise RuntimeError(f"Network/API error: {error}") from error

        pages_scanned += 1
        fetched += len(jobs)
        _emit(messages, progress, f"Scanned page {current_page}: {len(jobs)} offers.")
        if not jobs:
            _emit(messages, progress, "Provider returned no more offers.")
            break

        page_already_seen = 0
        page_newly_explored = 0
        for job in jobs:
            canonical_url = str(job.url)
            counted_as_new = False
            try:
                if has_explored_offer(
                    provider=job.source,
                    external_id=job.source_id,
                    canonical_url=canonical_url,
                    db_path=db_path,
                ):
                    already_seen += 1
                    page_already_seen += 1
                    continue

                newly_explored += 1
                page_newly_explored += 1
                counted_as_new = True
                if not job.description.strip():
                    filtered_out += 1
                    record_explored_job(
                        job,
                        status="filtered_out",
                        reason="missing_description",
                        db_path=db_path,
                    )
                    if target_new_offers is not None and newly_explored >= target_new_offers:
                        break
                    continue

                existing_offer_id = find_existing_offer_id(job, db_path=db_path)
                if existing_offer_id is not None:
                    evaluation = evaluate_job(job, profile=candidate_profile, config=rule_config)
                    save_screening_result(
                        db_path=db_path,
                        offer_id=existing_offer_id,
                        profile_path=str(profile_path),
                        evaluation=evaluation,
                        threshold=screening_threshold,
                    )
                    upsert_stats = UpsertStats(fetched=1, inserted=0, updated=0)
                    if find_existing_offer_id_by_url(canonical_url, db_path=db_path) is not None:
                        upsert_stats = upsert_offers([job], db_path=db_path)
                    updated += upsert_stats.updated
                    record_explored_job(
                        job,
                        status="updated" if upsert_stats.updated else "duplicate",
                        reason="already_in_offers",
                        db_path=db_path,
                    )
                    if target_new_offers is not None and newly_explored >= target_new_offers:
                        break
                    continue

                evaluation = evaluate_job(job, profile=candidate_profile, config=rule_config)
                if evaluation.normalized_score < screening_threshold:
                    filtered_out += 1
                    record_explored_job(
                        job,
                        status="filtered_out",
                        reason="rule_filter_failed",
                        db_path=db_path,
                    )
                    if target_new_offers is not None and newly_explored >= target_new_offers:
                        break
                    continue

                upsert_stats = upsert_offers([job], db_path=db_path)
                inserted += upsert_stats.inserted
                updated += upsert_stats.updated
                offer_id = find_existing_offer_id(job, db_path=db_path)
                if offer_id is not None:
                    save_screening_result(
                        db_path=db_path,
                        offer_id=offer_id,
                        profile_path=str(profile_path),
                        evaluation=evaluation,
                        threshold=screening_threshold,
                    )
                record_explored_job(
                    job,
                    status="inserted" if upsert_stats.inserted else "updated",
                    reason=None,
                    db_path=db_path,
                )
                matches.append((job, evaluation))
                if target_new_offers is not None and newly_explored >= target_new_offers:
                    break
            except Exception as error:
                errors += 1
                if not counted_as_new:
                    newly_explored += 1
                    page_newly_explored += 1
                record_explored_job(
                    job,
                    status="error",
                    reason=str(error),
                    db_path=db_path,
                )
                if target_new_offers is not None and newly_explored >= target_new_offers:
                    break

        if page_already_seen == len(jobs) and page_newly_explored == 0:
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
    debug_prompt: bool = False,
    progress: ProgressCallback | None = None,
) -> RankWorkflowResult:
    messages: list[str] = []
    _emit(messages, progress, f"Loading profile {profile_path}.")
    candidate_profile = load_profile(profile_path)
    rule_config = load_rule_scoring_config(weights_path)

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

    evaluated_jobs = [
        (stored_offer, evaluate_job(stored_offer.job, profile=candidate_profile, config=rule_config))
        for stored_offer in selected_offers
    ]
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
            llm_provider.check_ready()

        for index, (stored_offer, rule_evaluation) in enumerate(candidates, start=1):
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
