from __future__ import annotations

from tests._sqlite_review_shared import *


class FastBackfillTests(BaseSqliteReviewTests):
    def test_fast_backfill_falls_back_when_metadata_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            new = _source_job("new", "https://example.com/new", "C++ Simulation")

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", return_value=[new]):
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    min_score=0,
                    new_offers=1,
                    max_pages=1,
                    exploration_mode="fast_backfill",
                )

            self.assertEqual(result.stats.inserted, 1)

    def test_fast_backfill_processes_new_top_offers_before_previous_newest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            self._save_fetch_scope_metadata(
                db_path=db_path,
                newest_id="previous-newest",
                oldest_id="previous-oldest",
                last_explored_page=4,
            )
            new_top = _source_job("new-top", "https://example.com/new-top", "C++ Simulation New")
            previous_newest = _source_job("previous-newest", "https://example.com/previous-newest", "Previous")
            previous_oldest = _source_job("previous-oldest", "https://example.com/previous-oldest", "Previous Oldest")

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", side_effect=[[new_top, previous_newest], [previous_oldest]]):
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    min_score=0,
                    max_pages=2,
                    exploration_mode="fast_backfill",
                )

            self.assertEqual([job.title for job, _ in result.matches], ["C++ Simulation New"])
            self.assertEqual(result.stats.inserted, 1)

    def test_fast_backfill_jumps_to_previous_last_explored_page_minus_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            self._save_fetch_scope_metadata(
                db_path=db_path,
                newest_id="previous-newest",
                oldest_id="previous-oldest",
                last_explored_page=4,
            )
            previous_newest = _source_job("previous-newest", "https://example.com/previous-newest", "Previous")

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", side_effect=[[previous_newest], []]) as fetch_mock:
                fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    min_score=0,
                    pages=2,
                    exploration_mode="fast_backfill",
                )

            self.assertEqual([call.kwargs["page"] for call in fetch_mock.call_args_list], [1, 3])

    def test_fast_backfill_skips_until_previous_oldest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            self._save_fetch_scope_metadata(
                db_path=db_path,
                newest_id="previous-newest",
                oldest_id="previous-oldest",
                last_explored_page=4,
            )
            previous_newest = _source_job("previous-newest", "https://example.com/previous-newest", "Previous")
            skipped = _source_job("skipped", "https://example.com/skipped", "Skipped")
            previous_oldest = _source_job("previous-oldest", "https://example.com/previous-oldest", "Previous Oldest")
            older = _source_job("older", "https://example.com/older", "C++ Simulation Older")

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", side_effect=[[previous_newest], [skipped], [previous_oldest, older]]):
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    min_score=0,
                    pages=3,
                    exploration_mode="fast_backfill",
                )

            records = list_explored_offers(db_path)
            self.assertEqual([job.title for job, _ in result.matches], ["C++ Simulation Older"])
            self.assertNotIn("skipped", {record["external_id"] for record in records})
            self.assertIn("older", {record["external_id"] for record in records})

    def test_fast_backfill_resumes_normal_dedupe_after_previous_oldest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            self._save_fetch_scope_metadata(
                db_path=db_path,
                newest_id="previous-newest",
                oldest_id="previous-oldest",
                last_explored_page=4,
            )
            previous_newest = _source_job("previous-newest", "https://example.com/previous-newest", "Previous")
            previous_oldest = _source_job("previous-oldest", "https://example.com/previous-oldest", "Previous Oldest")
            already_seen = _source_job("already-seen", "https://example.com/already-seen", "Already Seen")
            record_explored_job(already_seen, status="inserted", db_path=db_path)

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", side_effect=[[previous_newest], [previous_oldest, already_seen]]):
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    min_score=0,
                    pages=2,
                    exploration_mode="fast_backfill",
                )

            self.assertEqual(result.stats.already_seen, 1)
            self.assertEqual(result.stats.inserted, 0)

