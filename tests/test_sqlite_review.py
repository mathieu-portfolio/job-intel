from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.models.job import JobOffer
from app.storage.sqlite import (
    clear_rankings,
    create_ranking_run,
    exclude_existing_offers,
    list_explored_offers,
    list_ranked_offers,
    list_unranked_review_offers,
    record_explored_job,
    save_ranking,
    upsert_offers,
)
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


class SqliteReviewTests(unittest.TestCase):
    def test_exclude_existing_offers_uses_source_id_and_url_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            upsert_offers(
                [
                    _source_job("source-1", "https://example.com/old-url", "Known by ID"),
                    _source_job(None, "https://example.com/known-url", "Known by URL"),
                ],
                db_path=db_path,
            )

            new_jobs = exclude_existing_offers(
                [
                    _source_job("source-1", "https://example.com/new-url", "Duplicate ID"),
                    _source_job(None, "https://example.com/known-url", "Duplicate URL"),
                    _source_job("source-2", "https://example.com/new", "New"),
                ],
                db_path=db_path,
            )

            self.assertEqual([job.title for job in new_jobs], ["New"])

    def test_explored_offer_records_are_inserted_and_updated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            job = _source_job("source-1", "https://example.com/job", "Tracked")

            record_explored_job(
                job,
                status="filtered_out",
                reason="rule_filter_failed",
                db_path=db_path,
                seen_at="2026-05-24T12:00:00",
            )
            record_explored_job(
                job,
                status="duplicate",
                reason="already_seen",
                db_path=db_path,
                seen_at="2026-05-24T12:05:00",
            )

            records = list_explored_offers(db_path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["status"], "duplicate")
            self.assertEqual(records[0]["reason"], "already_seen")
            self.assertEqual(records[0]["first_seen_at"], "2026-05-24T12:00:00")
            self.assertEqual(records[0]["last_seen_at"], "2026-05-24T12:05:00")

    def test_fetch_workflow_skips_already_explored_offers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            existing = _source_job("source-1", "https://example.com/old-url", "Known")
            new = _source_job("source-2", "https://example.com/new", "C++ Simulation")
            record_explored_job(
                existing,
                status="filtered_out",
                reason="rule_filter_failed",
                db_path=db_path,
            )

            with patch("app.workflows.fetch_arbeitnow", return_value=[existing, new]):
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    min_score=0,
                )

            self.assertEqual(result.stats.fetched, 2)
            self.assertEqual(result.stats.inserted, 1)
            self.assertEqual(result.stats.already_seen, 1)
            self.assertEqual([job.title for job, _ in result.matches], ["C++ Simulation"])

    def test_fetch_workflow_collects_target_new_offers_across_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            first = _source_job("source-1", "https://example.com/one", "C++ Simulation One")
            second = _source_job("source-2", "https://example.com/two", "C++ Simulation Two")

            with patch("app.workflows.fetch_arbeitnow", side_effect=[[first], [second]]):
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    min_score=0,
                    new_offers=2,
                    max_pages=5,
                )

            self.assertEqual(result.stats.pages_scanned, 2)
            self.assertEqual(result.stats.inserted, 2)
            self.assertEqual(result.matched_count, 2)

    def test_fetch_workflow_stops_at_max_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            first = _source_job("source-1", "https://example.com/one", "C++ Simulation One")
            second = _source_job("source-2", "https://example.com/two", "C++ Simulation Two")

            with patch("app.workflows.fetch_arbeitnow", side_effect=[[first], [second]]):
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    min_score=0,
                    new_offers=2,
                    max_pages=1,
                )

            self.assertEqual(result.stats.pages_scanned, 1)
            self.assertEqual(result.stats.inserted, 1)
            self.assertEqual(result.matched_count, 1)

    def test_fetch_workflow_tracks_filtered_offers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            filtered = _source_job("source-1", "https://example.com/filtered", "No Description")
            filtered.description = ""

            with patch("app.workflows.fetch_arbeitnow", return_value=[filtered]):
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    min_score=0,
                )

            records = list_explored_offers(db_path)
            self.assertEqual(result.stats.filtered_out, 1)
            self.assertEqual(result.stats.inserted, 0)
            self.assertEqual(records[0]["status"], "filtered_out")
            self.assertEqual(records[0]["reason"], "missing_description")

    def test_ai_only_filter_excludes_rule_only_hybrid_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            upsert_offers(
                [
                    _job("https://example.com/rule", "Rule"),
                    _job("https://example.com/ai", "AI"),
                    _job("https://example.com/hybrid-ai", "Hybrid AI"),
                    _job("https://example.com/hybrid-rule", "Hybrid Rule"),
                ],
                db_path=db_path,
            )

            rule_run = create_ranking_run(
                db_path=db_path,
                started_at="2026-05-24T12:00:00",
                algorithm="rules",
                model=None,
                profile_path="profiles/default.json",
                config={},
            )
            ai_run = create_ranking_run(
                db_path=db_path,
                started_at="2026-05-24T12:00:01",
                algorithm="ai",
                model="mock",
                profile_path="profiles/default.json",
                config={},
            )
            hybrid_run = create_ranking_run(
                db_path=db_path,
                started_at="2026-05-24T12:00:02",
                algorithm="hybrid",
                model="mock",
                profile_path="profiles/default.json",
                config={},
            )

            for offer_id, run_id, algorithm, model, raw_ai in [
                (1, rule_run, "rules", None, None),
                (2, ai_run, "ai", "mock", {"summary": "ai"}),
                (3, hybrid_run, "hybrid", "mock", {"summary": "hybrid-ai"}),
                (4, hybrid_run, "hybrid", "mock", None),
            ]:
                save_ranking(
                    db_path=db_path,
                    run_id=run_id,
                    offer_id=offer_id,
                    algorithm=algorithm,
                    model=model,
                    profile_path="profiles/default.json",
                    score=50,
                    recommendation="low",
                    summary="summary",
                    result=_result(raw_ai),
                )

            offers = list_ranked_offers(db_path=db_path, ai_only=True)
            self.assertEqual({offer["title"] for offer in offers}, {"AI", "Hybrid AI"})

    def test_location_filter_matches_partial_location_and_ignores_invalid_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            upsert_offers(
                [
                    _job("https://example.com/berlin", "Berlin", "Berlin, Germany"),
                    _job("https://example.com/paris", "Paris", "Paris, France"),
                ],
                db_path=db_path,
            )
            run_id = create_ranking_run(
                db_path=db_path,
                started_at="2026-05-24T12:00:00",
                algorithm="rules",
                model=None,
                profile_path="profiles/default.json",
                config={},
            )
            for offer_id in [1, 2]:
                save_ranking(
                    db_path=db_path,
                    run_id=run_id,
                    offer_id=offer_id,
                    algorithm="rules",
                    model=None,
                    profile_path="profiles/default.json",
                    score=50,
                    recommendation="low",
                    summary="summary",
                    result=_result(None),
                )

            self.assertEqual(
                [offer["title"] for offer in list_ranked_offers(db_path=db_path, location="ber")],
                ["Berlin"],
            )
            self.assertEqual(
                {offer["title"] for offer in list_ranked_offers(db_path=db_path, location="%%%")},
                {"Berlin", "Paris"},
            )

    def test_clear_rankings_keeps_offers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            upsert_offers([_job("https://example.com/offer", "Offer")], db_path=db_path)
            run_id = create_ranking_run(
                db_path=db_path,
                started_at="2026-05-24T12:00:00",
                algorithm="rules",
                model=None,
                profile_path="profiles/default.json",
                config={},
            )
            save_ranking(
                db_path=db_path,
                run_id=run_id,
                offer_id=1,
                algorithm="rules",
                model=None,
                profile_path="profiles/default.json",
                score=50,
                recommendation="low",
                summary="summary",
                result=_result(None),
            )

            self.assertEqual(len(list_ranked_offers(db_path=db_path)), 1)
            clear_rankings(db_path)
            self.assertEqual(list_ranked_offers(db_path=db_path), [])

    def test_unranked_review_offers_excludes_any_ranked_offer_and_filters_search(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            upsert_offers(
                [
                    _job("https://example.com/ranked", "Ranked C++", "Berlin"),
                    _job("https://example.com/unranked", "Unranked Simulation", "Paris"),
                    _job("https://example.com/other", "Frontend", "Remote"),
                ],
                db_path=db_path,
            )
            run_id = create_ranking_run(
                db_path=db_path,
                started_at="2026-05-24T12:00:00",
                algorithm="rules",
                model=None,
                profile_path="profiles/default.json",
                config={},
            )
            save_ranking(
                db_path=db_path,
                run_id=run_id,
                offer_id=1,
                algorithm="rules",
                model=None,
                profile_path="profiles/default.json",
                score=50,
                recommendation="low",
                summary="summary",
                result=_result(None),
            )

            offers = list_unranked_review_offers(db_path=db_path)
            self.assertEqual({offer["title"] for offer in offers}, {"Unranked Simulation", "Frontend"})

            filtered = list_unranked_review_offers(db_path=db_path, search="simulation")
            self.assertEqual([offer["title"] for offer in filtered], ["Unranked Simulation"])

    def test_rank_workflow_rules_mode_saves_summary_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            profile_path = Path(temp_dir) / "profile.json"
            profile_path.write_text(
                """
{
  "interests": ["C++", "simulation", "systems"],
  "preferred_domains": ["simulation"],
  "strengths": ["C++"],
  "location_preferences": ["Berlin"],
  "target_seniority": "mid"
}
""".strip(),
                encoding="utf-8",
            )
            upsert_offers(
                [_job("https://example.com/rules", "C++ Simulation Engineer", "Berlin")],
                db_path=db_path,
            )

            result = rank_offers(
                profile_path=profile_path,
                db_path=db_path,
                ranking_mode="rules",
                limit=1,
            )

            self.assertEqual(result.selected_count, 1)
            self.assertEqual(result.prefiltered_count, 1)
            self.assertEqual(result.ai_evaluation_count, 0)
            self.assertEqual(result.skipped_count, 0)
            self.assertEqual(result.saved_count, 1)
            self.assertEqual(len(list_ranked_offers(db_path=db_path)), 1)


if __name__ == "__main__":
    unittest.main()
