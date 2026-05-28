from __future__ import annotations

import tempfile
import unittest
import json
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import patch

from app.models.evaluation import AiJobEvaluation, RuleEvaluation
from app.models.job import JobOffer
from app.models.profile import CandidateProfile
from app.filtering.rules import RuleScoringConfig, evaluate_job
from app.storage.connection import init_db
from app.storage.exploration import (
    list_explored_offers,
    record_explored_job,
    save_exploration_metadata,
)
from app.storage.maintenance import clear_data, get_clear_plan, get_storage_counts, prune_storage
from app.storage.offers import exclude_existing_offers, find_existing_offer_id, update_offer_status, upsert_offers
from app.storage.reviews import (
    clear_rankings,
    create_ranking_run,
    list_ai_reviews,
    list_ranked_offers,
    list_screening_results,
    list_unranked_review_offers,
    save_ai_review,
    save_ranking,
)
from app.storage.scoring import list_screened_offers, save_offer_score, save_screening_result
from app.workflows import WorkflowCancelled, _exploration_scope_key, _exploration_scope_payload
from app.workflows import fetch_offers, rank_offers


def _job(url: str, title: str, location: str | None = None) -> JobOffer:
    return JobOffer(
        source="test",
        title=title,
        company="Example",
        location=location,
        url=url,
        description="systems engineering",
        raw_json={},
    )


def _source_job(source_id: str | None, url: str, title: str) -> JobOffer:
    return JobOffer(
        source="test",
        source_id=source_id,
        title=title,
        company="Example",
        url=url,
        description="systems engineering",
        raw_json={},
    )


def _result(raw_ai: object | None) -> dict[str, object]:
    return {
        "job": {},
        "rule_evaluation": {},
        "raw_ai_evaluation": raw_ai,
        "final_decision": {"final_score": 50, "recommendation": "low"},
    }


def _evaluation(score: int) -> RuleEvaluation:
    return RuleEvaluation(
        score=score,
        normalized_score=score,
        decision="high" if score >= 75 else "low",
        reasoning=[f"score {score}"],
    )




class BaseSqliteReviewTests(unittest.TestCase):
    def _write_profile(
        self,
        path: Path,
        *,
        positive_signals: dict[str, int] | None = None,
        negative_signals: dict[str, int] | None = None,
        threshold: int = 40,
    ) -> None:
        path.write_text(
            json.dumps(
                {
                    "name": "Test profile",
                    "positive_signals": positive_signals or {},
                    "negative_signals": negative_signals or {},
                    "screening_threshold": threshold,
                }
            ),
            encoding="utf-8",
        )

    def _seed_clear_data(self, db_path: Path) -> int:
        record_explored_job(
            _source_job("explored", "https://example.com/explored", "Explored"),
            status="filtered_out",
            db_path=db_path,
        )
        upsert_offers([_job("https://example.com/offer", "Offer")], db_path=db_path)
        run_id = create_ranking_run(
            db_path=db_path,
            started_at="2026-05-24T12:00:00",
            algorithm="rules",
            model=None,
            profile_id="default",
            config={},
        )
        save_ranking(
            db_path=db_path,
            run_id=run_id,
            offer_id=1,
            algorithm="rules",
            model=None,
            profile_id="default",
            score=50,
            recommendation="low",
            summary="summary",
            result=_result(None),
        )
        return run_id

    def _seed_screened_offer(self, db_path: Path, profile_id: Path, job: JobOffer) -> int:
        upsert_offers([job], db_path=db_path)
        offer_id = find_existing_offer_id(job, db_path=db_path)
        self.assertIsNotNone(offer_id)
        evaluation = evaluate_job(job)
        save_offer_score(
            db_path=db_path,
            offer_id=offer_id,
            preset_id="balanced",
            evaluation=evaluation,
        )
        save_screening_result(
            db_path=db_path,
            offer_id=offer_id,
            profile_id=str(profile_id),
            evaluation=evaluation,
            threshold=0,
        )
        return int(offer_id)

    def _save_fetch_scope_metadata(
        self,
        *,
        db_path: Path,
        newest_id: str,
        oldest_id: str,
        last_explored_page: int,
        profile_path: Path = Path("profiles/default.json"),
    ) -> None:
        scope = _exploration_scope_payload(
            source="arbeitnow",
            query="c++ simulation",
            country="fr",
            where=None,
            profile_path=profile_path,
            min_score=None,
        )
        save_exploration_metadata(
            db_path=db_path,
            scope_key=_exploration_scope_key(scope),
            source="arbeitnow",
            scope=scope,
            newest_id=newest_id,
            oldest_id=oldest_id,
            last_explored_page=last_explored_page,
        )


__all__ = [name for name in globals() if not name.startswith('__')]
