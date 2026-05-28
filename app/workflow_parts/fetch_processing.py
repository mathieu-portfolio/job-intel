from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from app.workflow_parts.common import (
    CancellationCheck,
    JobOffer,
    ProgressCallback,
    RuleEvaluation,
    _emit,
    _raise_if_cancelled,
    evaluate_job,
    find_existing_offer_ids_batch,
    find_existing_offer_ids_by_url_batch,
    has_explored_offers_batch,
    open_connection,
    record_explored_jobs_batch,
    save_offer_scores_batch,
    save_screening_results_batch,
    upsert_offers_batch,
)


class FetchPageProcessor:
    def __init__(
        self,
        *,
        db_path: Path,
        profile_id: str,
        profile_path: Path,
        candidate_profile: Any,
        screening_preset: Any,
        target_unexplored_offers: int | None,
        screening_threshold: int,
        timing: dict[str, float],
        messages: list[str],
        progress: ProgressCallback | None,
        cancelled: CancellationCheck | None,
    ) -> None:
        self.db_path = db_path
        self.profile_id = profile_id
        self.profile_path = profile_path
        self.candidate_profile = candidate_profile
        self.screening_preset = screening_preset
        self.target_unexplored_offers = target_unexplored_offers
        self.screening_threshold = screening_threshold
        self.timing = timing
        self.messages = messages
        self.progress = progress
        self.cancelled = cancelled

        self.inserted = 0
        self.updated = 0
        self.already_seen = 0
        self.newly_explored = 0
        self.filtered_out = 0
        self.errors = 0
        self.matches: list[tuple[JobOffer, RuleEvaluation]] = []

    def process(self, jobs: list[JobOffer], *, page_counts: dict[str, int]) -> bool:
        if not jobs:
            return False

        batch_started_at = time.perf_counter()
        with open_connection(self.db_path) as connection:
            lookup_started_at = time.perf_counter()
            explored_identities = has_explored_offers_batch(
                connection,
                jobs[0].source,
                [(job.source_id, str(job.url)) for job in jobs],
                profile_id=self.profile_id,
            )
            self.timing["explored_lookup"] += time.perf_counter() - lookup_started_at

            jobs_to_score: list[JobOffer] = []
            explored_records: list[tuple[JobOffer, str, str | None, bool]] = []
            stop_after_page = False
            for job in jobs:
                _raise_if_cancelled(self.cancelled)
                canonical_url = str(job.url)
                if (job.source_id, canonical_url) in explored_identities:
                    self.already_seen += 1
                    page_counts["already_seen"] += 1
                    continue

                self.newly_explored += 1
                page_counts["newly_explored"] += 1
                if self.target_unexplored_offers is not None:
                    _emit(
                        self.messages,
                        self.progress,
                        f"Processed {self.newly_explored}/{self.target_unexplored_offers} new/unexplored offers.",
                    )

                if not job.description.strip():
                    self.filtered_out += 1
                    explored_records.append((job, "filtered_out", "missing_description", False))
                else:
                    jobs_to_score.append(job)

                if self.target_unexplored_offers is not None and self.newly_explored >= self.target_unexplored_offers:
                    stop_after_page = True
                    break

            lookup_started_at = time.perf_counter()
            existing_offer_ids = find_existing_offer_ids_batch(connection, jobs_to_score)
            existing_url_ids = find_existing_offer_ids_by_url_batch(
                connection,
                [str(job.url) for job in jobs_to_score],
            )
            self.timing["explored_lookup"] += time.perf_counter() - lookup_started_at

            score_rows: list[tuple[int, str, str, RuleEvaluation]] = []
            screening_rows: list[tuple[int, str, str, RuleEvaluation, int]] = []
            upsert_jobs: list[JobOffer] = []
            selected_evaluation_by_url: dict[str, RuleEvaluation] = {}

            for job in jobs_to_score:
                canonical_url = str(job.url)
                try:
                    scoring_started_at = time.perf_counter()
                    evaluation = evaluate_job(
                        job,
                        profile=self.candidate_profile,
                        config=self.screening_preset.weights,
                    )
                    self.timing["scoring"] += time.perf_counter() - scoring_started_at
                    selected_evaluation_by_url[canonical_url] = evaluation

                    if evaluation.decision == "skip":
                        self.filtered_out += 1
                        explored_records.append((job, "filtered_out", "must_match_failed", False))
                        continue

                    existing_offer_id = existing_offer_ids.get(canonical_url)
                    if existing_offer_id is not None:
                        screening_rows.append(
                            (existing_offer_id, self.profile_id, str(self.profile_path), evaluation, self.screening_threshold)
                        )
                        if canonical_url in existing_url_ids:
                            upsert_jobs.append(job)
                            explored_records.append((job, "updated", "already_in_offers", False))
                        else:
                            explored_records.append((job, "duplicate", "already_in_offers", False))
                        continue

                    upsert_jobs.append(job)
                    explored_records.append((job, "inserted", None, False))
                except Exception as error:
                    self.errors += 1
                    explored_records.append((job, "error", str(error), False))

            upsert_started_at = time.perf_counter()
            upsert_stats, upserted_offer_ids = upsert_offers_batch(connection, upsert_jobs)
            self.timing["offer_upsert"] += time.perf_counter() - upsert_started_at
            self.inserted += upsert_stats.inserted
            self.updated += upsert_stats.updated

            for job in upsert_jobs:
                canonical_url = str(job.url)
                offer_id = upserted_offer_ids.get(canonical_url)
                if offer_id is None:
                    continue
                evaluation = selected_evaluation_by_url[canonical_url]
                screening_rows.append((offer_id, self.profile_id, str(self.profile_path), evaluation, self.screening_threshold))
                if canonical_url not in existing_offer_ids:
                    self.matches.append((job, evaluation))

            score_started_at = time.perf_counter()
            save_offer_scores_batch(connection, score_rows)
            self.timing["score_persistence"] += time.perf_counter() - score_started_at

            screened_started_at = time.perf_counter()
            save_screening_results_batch(connection, screening_rows)
            self.timing["screened_persistence"] += time.perf_counter() - screened_started_at

            explored_started_at = time.perf_counter()
            record_explored_jobs_batch(
                connection,
                explored_records,
                profile_id=self.profile_id,
                profile_path=str(self.profile_path),
            )
            self.timing["explored_persistence"] += time.perf_counter() - explored_started_at

        _emit(
            self.messages,
            self.progress,
            (
                f"Processed page batch: {len(jobs)} provider rows in "
                f"{time.perf_counter() - batch_started_at:.2f}s."
            ),
        )
        return stop_after_page
