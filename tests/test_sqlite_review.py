from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.models.job import JobOffer
from app.storage.sqlite import (
    clear_rankings,
    create_ranking_run,
    list_ranked_offers,
    list_unranked_review_offers,
    save_ranking,
    upsert_offers,
)
from app.workflows import rank_offers


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


def _result(raw_ai: object | None) -> dict[str, object]:
    return {
        "job": {},
        "rule_evaluation": {},
        "raw_ai_evaluation": raw_ai,
        "final_decision": {"final_score": 50, "recommendation": "low"},
    }


class SqliteReviewTests(unittest.TestCase):
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
