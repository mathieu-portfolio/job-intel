from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.models.job import JobOffer
from app.storage.sqlite import upsert_offers
from app.ui import create_app


def _job(url: str, title: str, location: str | None = None) -> JobOffer:
    return JobOffer(
        source="test",
        title=title,
        company="Example",
        location=location,
        url=url,
        description="C++ simulation systems engineering",
        raw_json={},
    )


class UiWorkflowTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
