from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.models.job import JobOffer
from app.storage.exploration import record_explored_job
from app.storage.maintenance import get_storage_counts
from app.storage.offers import find_existing_offer_id, upsert_offers
from app.storage.reviews import (
    create_ranking_run,
    list_ranked_offers,
    list_unranked_review_offers,
    save_ranking,
)
from app.storage.scoring import save_offer_score, save_screening_result
from app.filtering.rules import evaluate_job
from app.ui import create_app
from app.workflows import ProviderSearchRequest


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
    def test_provider_search_request_accepts_real_arguments(self) -> None:
        request = ProviderSearchRequest(query="C++ developer", where="Paris")

        self.assertEqual(request.query, "C++ developer")
        self.assertEqual(request.where, "Paris")

    def assert_confirm_happens_before_loading_state(self, html: str) -> None:
        confirm_index = html.find("confirm(message)")
        disabled_index = html.find("button.disabled = true")
        self.assertNotEqual(confirm_index, -1)
        self.assertNotEqual(disabled_index, -1)
        self.assertLess(confirm_index, disabled_index)

    def test_ranked_page_renders_rank_workflow_without_fetch_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            upsert_offers([_job("https://example.com/berlin", "Berlin Job", "Berlin")], db_path=db_path)
            client = TestClient(create_app(db_path))

            response = client.get("/")

            self.assertEqual(response.status_code, 200)
            self.assertIn("Review with AI", response.text)
            self.assertIn("workflow-modal", response.text)
            self.assertIn("Reviewing offers with AI", response.text)
            self.assertIn("workflow-modal-remaining", response.text)
            self.assertIn("offers left to compute", response.text)
            self.assertIn("/workflows/progress/", response.text)
            self.assertIn("Loading screened offers", response.text)
            self.assertIn("Calling AI provider", response.text)
            self.assertIn("Saving reviews", response.text)
            self.assertIn("Default", response.text)
            self.assertIn("Mathieu", response.text)
            self.assertNotIn("Default weights", response.text)
            self.assertIn("Balanced", response.text)
            self.assertIn('value="Berlin"', response.text)
            self.assertIn("Clear AI reviews", response.text)
            self.assertIn("Maintenance", response.text)
            self.assertIn("Danger zone", response.text)
            self.assertIn("Screened offers to review", response.text)
            self.assertNotIn("Ranking strategy", response.text)
            self.assertNotIn("Balanced hybrid", response.text)
            self.assertNotIn("Fast rules preview", response.text)
            self.assertNotIn('name="ranking_mode"', response.text)
            self.assertNotIn("Fetch offers</button>", response.text)
            self.assert_confirm_happens_before_loading_state(response.text)

    def test_clear_rankings_action_uses_scope_service_and_reports_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            upsert_offers([_job("https://example.com/ranked", "Ranked")], db_path=db_path)
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
            self.assertIn("Deleted AI reviews", response.text)
            self.assertNotIn("Preparing workflow", response.text)
            self.assertEqual(len(list_ranked_offers(db_path=db_path)), 0)

    def test_fetched_page_renders_fetch_workflow_and_clear_fetched_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            upsert_offers([_job("https://example.com/fetched", "Fetched", "Paris")], db_path=db_path)
            client = TestClient(create_app(db_path))

            response = client.get("/explore")

            self.assertEqual(response.status_code, 200)
            self.assertIn("Fetch offers", response.text)
            self.assertIn("workflow-modal", response.text)
            self.assertIn("Fetching offers", response.text)
            self.assertIn("workflow-modal-remaining", response.text)
            self.assertIn("offers left to fetch", response.text)
            self.assertIn("Scanning provider pages", response.text)
            self.assertIn("Applying profile screening", response.text)
            self.assertIn("Saving screened offers", response.text)
            self.assertIn('name="location"', response.text)
            self.assertIn('value="Paris"', response.text)
            self.assertIn("Market", response.text)
            self.assertIn("France", response.text)
            self.assertIn("Germany", response.text)
            self.assertIn("United Kingdom", response.text)
            self.assertIn("United States", response.text)
            self.assertIn('data-provider="adzuna" hidden', response.text)
            self.assertIn("marketSelect.required = isAdzuna", response.text)
            self.assertIn('placeholder="Profile queries"', response.text)
            self.assertIn('name="use_profile_queries"', response.text)
            self.assertIn('value="50"', response.text)
            self.assertIn("Clear screened offers", response.text)
            self.assertIn("Danger zone", response.text)
            self.assertNotIn("Review with AI</button>", response.text)
            self.assertNotIn('value="c++ simulation"', response.text)
            self.assert_confirm_happens_before_loading_state(response.text)

    def test_adzuna_fetch_requires_market(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            client = TestClient(create_app(db_path))

            response = client.post(
                "/workflows/fetch",
                data={"source": "adzuna", "new_offers": "1", "max_pages": "1"},
                follow_redirects=True,
            )

            self.assertEqual(response.status_code, 200)
            self.assertIn("Fetch failed", response.text)
            self.assertIn("Market is required when fetching from Adzuna.", response.text)
            self.assertNotIn("Preparing workflow", response.text)

    def test_fetch_action_runs_with_mocked_provider_and_renders_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            db_path = base_path / "jobs.sqlite"
            profile_id = base_path / "profile.json"
            profile_id.write_text(
                """
{
  "interests": ["C++", "simulation", "systems"],
  "preferred_domains": ["simulation"],
  "strengths": ["C++"],
  "location_preferences": ["Paris"],
  "target_seniority": "mid"
}
""".strip(),
                encoding="utf-8",
            )
            job = _job("https://example.com/fetched-workflow", "C++ Simulation Engineer", "Paris")
            client = TestClient(create_app(db_path))

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", return_value=[job]):
                response = client.post(
                    "/workflows/fetch",
                    data={
                        "source": "arbeitnow",
                        "profile": str(profile_id),
                        "new_offers": "1",
                        "max_pages": "1",
                        "min_score": "0",
                    },
                    follow_redirects=True,
                )

            self.assertEqual(response.status_code, 200)
            self.assertIn("Fetch complete", response.text)
            self.assertIn("C++ Simulation Engineer", response.text)
            self.assertNotIn("ProviderSearchRequest() takes no arguments", response.text)
            self.assertNotIn("Preparing workflow", response.text)

    def test_clear_fetched_action_clears_offers_and_redirects_to_fetched_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            upsert_offers([_job("https://example.com/fetched", "Fetched")], db_path=db_path)
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
                result={},
            )
            client = TestClient(create_app(db_path))

            response = client.post(
                "/storage/clear",
                data={"scope": "offers", "redirect_to": "/explore"},
                follow_redirects=True,
            )

            self.assertEqual(response.status_code, 200)
            self.assertIn("Clear complete", response.text)
            self.assertIn("Explore", response.text)
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
            self.assertIn("Screened offers", response.text)
            self.assertIn("AI reviewed offers", response.text)
            self.assertIn("Clear explored tracking", response.text)
            self.assertIn("Clear all data", response.text)
            self.assert_confirm_happens_before_loading_state(response.text)

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
            profile_id = base_path / "profile.json"
            profile_id.write_text(
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
            job = _job("https://example.com/rules", "C++ Simulation Engineer", "Berlin")
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

            client = TestClient(create_app(db_path))
            response = client.post(
                "/workflows/rank",
                data={
                    "profile": str(profile_id),
                    "limit": "1",
                    "provider": "mock",
                },
                follow_redirects=True,
            )

            self.assertEqual(response.status_code, 200)
            self.assertIn("Rank complete", response.text)
            self.assertIn("Saved AI reviews", response.text)
            self.assertIn("C++ Simulation Engineer", response.text)
            self.assertNotIn("name &#39;_emit&#39; is not defined", response.text)
            self.assertNotIn("name '_emit' is not defined", response.text)
            self.assertNotIn("Preparing workflow", response.text)

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
            response = client.get("/explore", params={"q": "simulation"})

            self.assertEqual(response.status_code, 200)
            self.assertIn("Explore", response.text)
            self.assertIn("Simulation Engineer", response.text)
            self.assertNotIn("Web Engineer", response.text)

    def test_screened_offers_page_filters_by_selected_preset_score(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            jobs = [
                _job("https://example.com/sim", "Simulation Engineer", "Berlin"),
                _job(
                    "https://example.com/web",
                    "Web Engineer",
                    "Remote",
                    "frontend product engineering",
                ),
            ]
            upsert_offers(jobs, db_path=db_path)
            save_offer_score(
                db_path=db_path,
                offer_id=1,
                preset_id="balanced",
                evaluation=evaluate_job(jobs[0]),
            )
            save_offer_score(
                db_path=db_path,
                offer_id=2,
                preset_id="balanced",
                evaluation=evaluate_job(jobs[1]),
            )

            client = TestClient(create_app(db_path))
            response = client.get("/screened", params={"q": "simulation", "threshold": "0"})

            self.assertEqual(response.status_code, 200)
            self.assertIn("Preset", response.text)
            self.assertIn("Balanced", response.text)
            self.assertIn("Simulation Engineer", response.text)
            self.assertNotIn("Web Engineer", response.text)


if __name__ == "__main__":
    unittest.main()
