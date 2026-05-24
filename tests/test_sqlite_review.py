from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.models.job import JobOffer
from app.storage.sqlite import (
    clear_rankings,
    create_ranking_run,
    list_ranked_offers,
    save_ranking,
    upsert_offers,
)


def _job(url: str, title: str) -> JobOffer:
    return JobOffer(
        source="test",
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


if __name__ == "__main__":
    unittest.main()
