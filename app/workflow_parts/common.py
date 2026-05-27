from __future__ import annotations

import re
import time
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from app.models.profile import CandidateProfile
from app.sources.adzuna import fetch_adzuna
from app.sources.arbeitnow import fetch_arbeitnow
from app.storage.files import load_profile, profile_id_from_path
from app.storage.connection import DEFAULT_DB_PATH, init_db, open_connection
from app.storage.exploration import (
    get_exploration_metadata,
    has_explored_offers_batch,
    record_explored_jobs_batch,
    save_exploration_metadata,
)
from app.storage.maintenance import (
    DEFAULT_EXPLORED_CAPACITY,
    DEFAULT_RANKED_CAPACITY,
    DEFAULT_UNRANKED_CAPACITY,
    prune_storage,
)
from app.storage.models import PruneStats, StoredOffer, UpsertStats
from app.storage.offers import (
    find_existing_offer_ids_batch,
    find_existing_offer_ids_by_url_batch,
    select_unranked_offers,
    upsert_offers_batch,
)
from app.storage.reviews import create_ranking_run, save_ai_review, save_ranking
from app.storage.scoring import (
    find_screening_result_id,
    get_scoring_preset,
    list_scoring_presets,
    save_offer_scores_batch,
    save_screening_results_batch,
    select_screened_offers,
)


RankingMode = Literal["rules", "ai", "hybrid"]
FetchSource = Literal["arbeitnow", "adzuna"]
ExplorationMode = Literal["safe", "normal", "fast_backfill"]
RankedResult = tuple[StoredOffer, RuleEvaluation, AiJobEvaluation | None, FinalDecision]
ProgressCallback = Callable[[str], None]
CancellationCheck = Callable[[], bool]
FAST_BACKFILL_SKIP_PAGE_LIMIT = 5
DEFAULT_FETCH_CONCURRENCY = 1
DEFAULT_AI_RANKING_CONCURRENCY = 1



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
    request_summaries: list[FetchRequestSummary] = field(default_factory=list)
    provider_keys: frozenset[tuple[str, str]] = field(default_factory=frozenset)


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


@dataclass(frozen=True)
class ProviderSearchRequest:
    query: str
    where: str | None = None


@dataclass(frozen=True)
class FetchRequestSummary:
    query: str
    where: str | None
    pages_scanned: int
    provider_rows: int
    unique_provider_rows: int
    duplicate_provider_rows: int
    newly_explored: int
    already_seen: int
    filtered_out: int
    screened: int


@dataclass(frozen=True)
class RetryConfig:
    attempts: int = 1
    backoff_seconds: float = 0.0


def _validate_concurrency(value: int, *, name: str) -> int:
    if value < 1:
        raise ValueError(f"{name} must be at least 1.")
    return value


def _validate_retry_config(attempts: int, backoff_seconds: float, *, name: str) -> RetryConfig:
    if attempts < 1:
        raise ValueError(f"{name} attempts must be at least 1.")
    if backoff_seconds < 0:
        raise ValueError(f"{name} backoff must be zero or greater.")
    return RetryConfig(attempts=attempts, backoff_seconds=backoff_seconds)


def _run_with_retries(
    operation: Callable[[], object],
    *,
    retry_config: RetryConfig,
) -> object:
    last_error: Exception | None = None
    for attempt in range(1, retry_config.attempts + 1):
        try:
            return operation()
        except Exception as error:
            last_error = error
            if attempt >= retry_config.attempts:
                break
            if retry_config.backoff_seconds > 0:
                time.sleep(retry_config.backoff_seconds * attempt)
    if last_error is not None:
        raise last_error
    raise RuntimeError("Retry operation failed without raising an error.")


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


def _offer_provider_key(job: JobOffer) -> tuple[str, str]:
    return (job.source, _offer_exploration_id(job))


def _format_search_request(request: ProviderSearchRequest) -> str:
    query = request.query or "<broad>"
    return f"{query}{f' @ {request.where}' if request.where else ''}"


def _exploration_scope_payload(
    *,
    source: FetchSource,
    query: str,
    country: str,
    where: str | None,
    profile_path: Path,
    min_score: int | None,
) -> dict[str, object]:
    profile_id = profile_id_from_path(profile_path)
    return {
        "source": source,
        "query": query,
        "country": country,
        "where": where or "",
        "profile_id": profile_id,
        "profile_path": str(profile_path),
        "min_score": min_score,
    }


def _exploration_scope_key(scope: dict[str, object]) -> str:
    payload = json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _profile_search_locations(profile: CandidateProfile) -> list[str | None]:
    terms: list[str] = [str(location) for location in profile.location_preferences]
    location_category = profile.signals.get("location_preferences")
    if location_category is not None:
        terms.extend(item.term for item in location_category.items)
    locations = _dedupe_preserve_order(terms)
    return locations or [None]


def iter_profile_search_requests(
    profile: CandidateProfile,
    provider: FetchSource,
    *,
    query: str | None = None,
    where: str | None = None,
    use_profile_queries: bool = True,
) -> list[ProviderSearchRequest]:
    manual_query = (query or "").strip()
    manual_where = (where or "").strip() or None
    if provider != "adzuna":
        return [ProviderSearchRequest(query=manual_query, where=manual_where)]
    if manual_query or not use_profile_queries:
        return [ProviderSearchRequest(query=manual_query, where=manual_where)]

    queries = _dedupe_preserve_order(
        [
            search_query
            for language_queries in profile.search_queries.values()
            for search_query in language_queries
        ]
    )
    if not queries:
        return [ProviderSearchRequest(query=manual_query, where=manual_where)]

    locations = [manual_where] if manual_where else _profile_search_locations(profile)
    return [
        ProviderSearchRequest(query=search_query, where=search_where)
        for search_query in queries
        for search_where in locations
    ]


# Split workflow modules intentionally use `from app.workflow_parts.common import *`.
# Include underscored helpers so the split keeps the old monolith's private wiring.
__all__ = [name for name in globals() if not name.startswith("__")]
