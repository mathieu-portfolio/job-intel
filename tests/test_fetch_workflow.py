from __future__ import annotations

from tests._sqlite_review_shared import *


class FetchWorkflowTests(BaseSqliteReviewTests):
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

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", return_value=[existing, new]):
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    min_score=0,
                )

            self.assertEqual(result.stats.fetched, 2)
            self.assertEqual(result.stats.inserted, 1)
            self.assertEqual(result.stats.already_seen, 1)
            self.assertEqual([job.title for job, _ in result.matches], ["C++ Simulation"])

    def test_fetch_workflow_skips_seen_page_and_continues_to_next_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            seen_jobs = [
                _source_job(f"seen-{index}", f"https://example.com/seen-{index}", f"Seen {index}")
                for index in range(3)
            ]
            new = _source_job("source-new", "https://example.com/new", "C++ Simulation")
            for job in seen_jobs:
                record_explored_job(job, status="inserted", db_path=db_path)

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", side_effect=[seen_jobs, [new]]) as fetch_mock:
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    min_score=0,
                    new_offers=1,
                    max_pages=2,
                )

            self.assertEqual([call.kwargs["page"] for call in fetch_mock.call_args_list], [1, 2])
            self.assertEqual(result.stats.pages_scanned, 2)
            self.assertEqual(result.stats.already_seen, 3)
            self.assertEqual(result.stats.newly_explored, 1)
            self.assertEqual(result.stats.inserted, 1)

    def test_fetch_workflow_already_seen_does_not_count_toward_new_offers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            seen = _source_job("seen", "https://example.com/seen", "Seen")
            new = _source_job("new", "https://example.com/new", "C++ Simulation")
            record_explored_job(seen, status="filtered_out", db_path=db_path)

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", side_effect=[[seen], [new]]):
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    min_score=0,
                    new_offers=1,
                    max_pages=2,
                )

            self.assertEqual(result.stats.pages_scanned, 2)
            self.assertEqual(result.stats.already_seen, 1)
            self.assertEqual(result.stats.newly_explored, 1)
            self.assertEqual(result.stats.inserted, 1)

    def test_fetch_workflow_collects_target_new_offers_across_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            first = _source_job("source-1", "https://example.com/one", "C++ Simulation One")
            second = _source_job("source-2", "https://example.com/two", "C++ Simulation Two")

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", side_effect=[[first], [second]]):
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

    def test_fetch_workflow_stops_at_new_offers_after_newly_explored_processed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            filtered = _source_job("filtered", "https://example.com/filtered", "No Description")
            filtered.description = ""
            inserted = _source_job("inserted", "https://example.com/inserted", "C++ Simulation")
            extra = _source_job("extra", "https://example.com/extra", "C++ Simulation Extra")

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", side_effect=[[filtered, inserted], [extra]]) as fetch_mock:
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    min_score=0,
                    new_offers=2,
                    max_pages=5,
                )

            self.assertEqual(fetch_mock.call_count, 1)
            self.assertEqual(result.stats.pages_scanned, 1)
            self.assertEqual(result.stats.newly_explored, 2)
            self.assertEqual(result.stats.filtered_out, 1)
            self.assertEqual(result.stats.inserted, 1)

    def test_fetch_workflow_stops_at_max_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            first = _source_job("source-1", "https://example.com/one", "C++ Simulation One")
            second = _source_job("source-2", "https://example.com/two", "C++ Simulation Two")

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", side_effect=[[first], [second]]):
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    min_score=0,
                    new_offers=2,
                    max_pages=1,
                )

            self.assertEqual(result.stats.pages_scanned, 1)
            self.assertEqual(result.stats.inserted, 1)
            self.assertEqual(result.stats.newly_explored, 1)
            self.assertEqual(result.matched_count, 1)

    def test_fetch_workflow_stops_after_max_seen_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            seen_one = _source_job("seen-1", "https://example.com/seen-1", "Seen One")
            seen_two = _source_job("seen-2", "https://example.com/seen-2", "Seen Two")
            new = _source_job("new", "https://example.com/new", "C++ Simulation")
            for job in [seen_one, seen_two]:
                record_explored_job(job, status="inserted", db_path=db_path)

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", side_effect=[[seen_one], [seen_two], [new]]):
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    min_score=0,
                    new_offers=1,
                    max_pages=5,
                    max_seen_pages=2,
                )

            self.assertEqual(result.stats.pages_scanned, 2)
            self.assertEqual(result.stats.already_seen, 2)
            self.assertEqual(result.stats.newly_explored, 0)
            self.assertEqual(result.stats.inserted, 0)

    def test_fetch_workflow_tracks_filtered_offers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            filtered = _source_job("source-1", "https://example.com/filtered", "No Description")
            filtered.description = ""

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", return_value=[filtered]):
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    min_score=0,
                    new_offers=1,
                    max_pages=5,
                )

            records = list_explored_offers(db_path)
            self.assertEqual(result.stats.newly_explored, 1)
            self.assertEqual(result.stats.filtered_out, 1)
            self.assertEqual(result.stats.inserted, 0)
            self.assertEqual(records[0]["status"], "filtered_out")
            self.assertEqual(records[0]["reason"], "missing_description")

    def test_fetch_workflow_persists_profile_driven_screening_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            db_path = base_path / "jobs.sqlite"
            profile_id = base_path / "profile.json"
            self._write_profile(profile_id, positive_signals={"simulation": 50}, threshold=40)
            matching = _source_job("source-1", "https://example.com/match", "Simulation Engineer")

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", return_value=[matching]):
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    profile_path=profile_id,
                    new_offers=1,
                    max_pages=1,
                )

            screenings = list_screening_results(db_path)
            self.assertEqual(result.stats.inserted, 1)
            self.assertEqual(len(screenings), 1)
            self.assertEqual(screenings[0]["title"], "Simulation Engineer")
            self.assertEqual(screenings[0]["profile_id"], "profile")
            self.assertEqual(screenings[0]["passed"], 1)
            self.assertTrue(any("Fetch timing:" in message for message in result.messages))

    def test_fetch_page_concurrency_limit_is_respected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            active = 0
            max_active = 0
            lock = threading.Lock()

            def fetch_page(*, page: int) -> list[JobOffer]:
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                try:
                    time.sleep(0.05)
                    return [_source_job(f"page-{page}", f"https://example.com/page-{page}", f"Page {page}")]
                finally:
                    with lock:
                        active -= 1

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", side_effect=fetch_page):
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    pages=4,
                    min_score=0,
                    fetch_concurrency=2,
                )

            self.assertEqual(result.stats.errors, 0)
            self.assertEqual(result.stats.inserted, 4)
            self.assertLessEqual(max_active, 2)
            self.assertGreater(max_active, 1)

    def test_fetch_new_offers_parallel_pages_are_faster_than_serial(self) -> None:
        def fetch_page(*, page: int) -> list[JobOffer]:
            time.sleep(0.2)
            return [_source_job(f"page-{page}", f"https://example.com/page-{page}", f"Page {page}")]

        with tempfile.TemporaryDirectory() as serial_dir, tempfile.TemporaryDirectory() as parallel_dir:
            serial_db = Path(serial_dir) / "jobs.sqlite"
            parallel_db = Path(parallel_dir) / "jobs.sqlite"

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", side_effect=fetch_page):
                started_at = time.perf_counter()
                serial_result = fetch_offers(
                    source="arbeitnow",
                    db_path=serial_db,
                    new_offers=6,
                    max_pages=6,
                    min_score=0,
                    fetch_concurrency=1,
                )
                serial_elapsed = time.perf_counter() - started_at

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", side_effect=fetch_page):
                started_at = time.perf_counter()
                parallel_result = fetch_offers(
                    source="arbeitnow",
                    db_path=parallel_db,
                    new_offers=6,
                    max_pages=6,
                    min_score=0,
                    fetch_concurrency=3,
                )
                parallel_elapsed = time.perf_counter() - started_at

            self.assertEqual(serial_result.stats.inserted, 6)
            self.assertEqual(parallel_result.stats.inserted, 6)
            self.assertLess(parallel_elapsed, serial_elapsed * 0.85)
            self.assertTrue(any("page 2 started" in message for message in parallel_result.messages))
            self.assertTrue(any("page 1 completed" in message for message in parallel_result.messages))

    def test_fetch_page_failure_does_not_drop_successful_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"

            def fetch_page(*, page: int) -> list[JobOffer]:
                if page == 2:
                    raise RuntimeError("provider down")
                return [_source_job(f"page-{page}", f"https://example.com/page-{page}", f"Page {page}")]

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", side_effect=fetch_page):
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    pages=3,
                    min_score=0,
                    fetch_concurrency=2,
                )

            self.assertEqual(result.stats.errors, 1)
            self.assertEqual(result.stats.inserted, 2)
            self.assertEqual(len(list_screening_results(db_path)), 2)

    def test_fetch_workflow_uses_profile_signals_not_hardcoded_terms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            db_path = base_path / "jobs.sqlite"
            profile_id = base_path / "profile.json"
            profile_id.write_text(json.dumps({"must_match": {"any": [{"term": "python"}]}, "positive_signals": {"python": 50}, "screening_threshold": 40}), encoding="utf-8")
            old_global_match = _source_job("source-1", "https://example.com/cpp", "C++ Simulation Engineer")

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", return_value=[old_global_match]):
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    profile_path=profile_id,
                    new_offers=1,
                    max_pages=1,
                )

            self.assertEqual(result.stats.inserted, 0)
            self.assertEqual(result.stats.filtered_out, 1)
            self.assertEqual(list_screening_results(db_path), [])

    def test_adzuna_fetch_uses_profile_search_queries_when_query_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            db_path = base_path / "jobs.sqlite"
            profile_id = base_path / "profile.json"
            profile_id.write_text(
                json.dumps(
                    {
                        "search_queries": {
                            "en": ["systems engineer"],
                            "fr": ["ingénieur simulation"],
                        },
                        "screening_threshold": 0,
                    }
                ),
                encoding="utf-8",
            )
            first = _source_job("adzuna-1", "https://example.com/adzuna-1", "Systems Engineer")
            second = _source_job("adzuna-2", "https://example.com/adzuna-2", "Simulation Engineer")

            with patch("app.workflow_parts.fetch.fetch_adzuna", side_effect=[[first], [second]]) as fetch_mock:
                result = fetch_offers(
                    source="adzuna",
                    db_path=db_path,
                    profile_path=profile_id,
                    query="",
                    country="fr",
                    new_offers=2,
                    max_pages=1,
                    min_score=0,
                )

            self.assertEqual(result.stats.inserted, 2)
            self.assertEqual(
                [call.kwargs["query"] for call in fetch_mock.call_args_list],
                ["systems engineer", "ingénieur simulation"],
            )
            self.assertTrue(any("Fetch plan: 2 profile search requests" in message for message in result.messages))

    def test_adzuna_manual_query_overrides_profile_search_queries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            db_path = base_path / "jobs.sqlite"
            profile_id = base_path / "profile.json"
            profile_id.write_text(
                json.dumps({"search_queries": {"en": ["systems engineer"]}, "screening_threshold": 0}),
                encoding="utf-8",
            )
            offer = _source_job("manual", "https://example.com/manual", "Manual Query Engineer")

            with patch("app.workflow_parts.fetch.fetch_adzuna", return_value=[offer]) as fetch_mock:
                result = fetch_offers(
                    source="adzuna",
                    db_path=db_path,
                    profile_path=profile_id,
                    query="manual query",
                    country="fr",
                    new_offers=1,
                    max_pages=1,
                    min_score=0,
                )

            self.assertEqual(result.stats.inserted, 1)
            self.assertEqual(len(fetch_mock.call_args_list), 1)
            self.assertEqual(fetch_mock.call_args.kwargs["query"], "manual query")

    def test_adzuna_profile_query_duplicates_are_deduped_across_queries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            db_path = base_path / "jobs.sqlite"
            profile_id = base_path / "profile.json"
            profile_id.write_text(
                json.dumps(
                    {
                        "search_queries": {
                            "en": ["systems engineer", "software engineer"],
                        },
                        "screening_threshold": 0,
                    }
                ),
                encoding="utf-8",
            )
            duplicate = _source_job("same-offer", "https://example.com/same", "Systems Engineer")

            with patch("app.workflow_parts.fetch.fetch_adzuna", side_effect=[[duplicate], [duplicate]]):
                result = fetch_offers(
                    source="adzuna",
                    db_path=db_path,
                    profile_path=profile_id,
                    query="",
                    country="fr",
                    new_offers=2,
                    max_pages=1,
                    min_score=0,
                )

            self.assertEqual(result.stats.inserted, 1)
            self.assertEqual(result.stats.already_seen, 1)
            self.assertEqual(len(list_explored_offers(db_path)), 1)

