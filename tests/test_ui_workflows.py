from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.models.job import JobOffer
from app.storage.sqlite import (
    create_ranking_run,
    get_storage_counts,
    list_ranked_offers,
    list_unranked_review_offers,
    record_explored_job,
    save_ranking,
    upsert_offers,
)
from app.ui import create_app


def _job(
    url: str,
    title: str,
    location: str | None = None,
    description: str = "C++ simulation systems engineering",
) -> JobOffer:
    return JobOffer(
        source="test",
        title=title,
        company="Example",
        location=location,
        url=url,
        description=description,
        raw_json={},
    )


class UiWorkflowTests(unittest.TestCase):
    def test_ranked_page_renders_rank_workflow_without_fetch_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            client = TestClient(create_app(db_path))

            response = client.get("/")

            self.assertEqual(response.status_code, 200)
            self.assertIn("Rank offers", response.text)
            self.assertIn("Clear rankings", response.text)
            self.assertIn("Maintenance", response.text)
            self.assertNotIn("Fetch offers</button>", response.text)

    def test_clear_rankings_action_uses_scope_service_and_reports_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            upsert_offers([_job("https://example.com/ranked", "Ranked")], db_path=db_path)
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
                result={},
            )
            client = TestClient(create_app(db_path))

            response = client.post(
                "/storage/clear",
                data={"scope": "rankings", "redirect_to": "/"},
                follow_redirects=True,
            )

            self.assertEqual(response.status_code, 200)
            self.assertIn("Clear complete", response.text)
            self.assertIn("Deleted rankings", response.text)
            self.assertEqual(len(list_ranked_offers(db_path=db_path)), 0)

    def test_fetched_page_renders_fetch_workflow_and_clear_fetched_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            upsert_offers([_job("https://example.com/fetched", "Fetched")], db_path=db_path)
            client = TestClient(create_app(db_path))

            response = client.get("/offers")

            self.assertEqual(response.status_code, 200)
            self.assertIn("Fetch offers", response.text)
            self.assertIn("Clear fetched offers", response.text)
            self.assertNotIn("Rank offers</button>", response.text)

    def test_clear_fetched_action_clears_offers_and_redirects_to_fetched_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            upsert_offers([_job("https://example.com/fetched", "Fetched")], db_path=db_path)
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
                result={},
            )
            client = TestClient(create_app(db_path))

            response = client.post(
                "/storage/clear",
                data={"scope": "offers", "redirect_to": "/offers"},
                follow_redirects=True,
            )

            self.assertEqual(response.status_code, 200)
            self.assertIn("Clear complete", response.text)
            self.assertIn("Fetched offers", response.text)
            self.assertEqual(len(list_unranked_review_offers(db_path=db_path)), 0)
            self.assertEqual(len(list_ranked_offers(db_path=db_path)), 0)

    def test_maintenance_page_shows_counts_and_global_clear_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            record_explored_job(
                _job("https://example.com/explored", "Explored"),
                status="filtered_out",
                db_path=db_path,
            )
            upsert_offers([_job("https://example.com/unranked", "Unranked")], db_path=db_path)
            client = TestClient(create_app(db_path))

            response = client.get("/maintenance")

            self.assertEqual(response.status_code, 200)
            self.assertIn("Explored tracking", response.text)
            self.assertIn("Fetched/unranked offers", response.text)
            self.assertIn("Ranked offers", response.text)
            self.assertIn("Clear explored tracking", response.text)
            self.assertIn("Clear all data", response.text)

    def test_maintenance_clear_all_clears_all_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            record_explored_job(
                _job("https://example.com/explored", "Explored"),
                status="filtered_out",
                db_path=db_path,
            )
            upsert_offers([_job("https://example.com/ranked", "Ranked")], db_path=db_path)
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
                result={},
            )
            client = TestClient(create_app(db_path))

            response = client.post(
                "/storage/clear",
                data={"scope": "all", "redirect_to": "/maintenance"},
                follow_redirects=True,
            )

            self.assertEqual(response.status_code, 200)
            self.assertIn("Clear complete", response.text)
            self.assertEqual(get_storage_counts(db_path).explored, 0)
            self.assertEqual(get_storage_counts(db_path).unranked, 0)
            self.assertEqual(get_storage_counts(db_path).ranked, 0)

    def test_rank_action_runs_rules_workflow_and_renders_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            db_path = base_path / "jobs.sqlite"
            profile_path = base_path / "profile.json"
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

            client = TestClient(create_app(db_path))
            response = client.post(
                "/workflows/rank",
                data={
                    "profile": str(profile_path),
                    "ranking_mode": "rules",
                    "limit": "1",
                    "min_score": "40",
                },
                follow_redirects=True,
            )

            self.assertEqual(response.status_code, 200)
            self.assertIn("Rank complete", response.text)
            self.assertIn("Saved rankings", response.text)
            self.assertIn("C++ Simulation Engineer", response.text)

    def test_fetched_offers_page_lists_unranked_offers_with_search(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            upsert_offers(
                [
                    _job("https://example.com/sim", "Simulation Engineer", "Berlin"),
                    _job(
                        "https://example.com/web",
                        "Web Engineer",
                        "Remote",
                        "frontend product engineering",
                    ),
                ],
                db_path=db_path,
            )

            client = TestClient(create_app(db_path))
            response = client.get("/offers", params={"q": "simulation"})

            self.assertEqual(response.status_code, 200)
            self.assertIn("Fetched offers", response.text)
            self.assertIn("Simulation Engineer", response.text)
            self.assertNotIn("Web Engineer", response.text)


if __name__ == "__main__":
    unittest.main()
