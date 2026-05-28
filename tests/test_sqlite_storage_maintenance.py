from __future__ import annotations

from tests._sqlite_review_shared import *


class SqliteStorageMaintenanceTests(BaseSqliteReviewTests):
    def test_clear_scope_rankings_keeps_offers_and_explored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            self._seed_clear_data(db_path)

            plan = get_clear_plan(db_path=db_path, scope="rankings")
            result = clear_data(db_path=db_path, scope="rankings")

            self.assertEqual(plan.rankings, 1)
            self.assertEqual(result.rankings, 1)
            self.assertEqual(list_ranked_offers(db_path=db_path), [])
            self.assertEqual(len(list_unranked_review_offers(db_path=db_path)), 1)
            self.assertEqual(len(list_explored_offers(db_path)), 1)

    def test_clear_scope_offers_clears_dependent_rankings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            self._seed_clear_data(db_path)

            result = clear_data(db_path=db_path, scope="offers")

            self.assertEqual(result.offers, 1)
            self.assertEqual(result.rankings, 1)
            self.assertEqual(list_unranked_review_offers(db_path=db_path), [])
            self.assertEqual(list_ranked_offers(db_path=db_path), [])
            self.assertEqual(len(list_explored_offers(db_path)), 1)

    def test_clear_scope_explored_keeps_offers_and_rankings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            self._seed_clear_data(db_path)

            result = clear_data(db_path=db_path, scope="explored")

            self.assertEqual(result.explored, 1)
            self.assertEqual(list_explored_offers(db_path), [])
            self.assertEqual(len(list_ranked_offers(db_path=db_path)), 1)

    def test_clear_scope_all_clears_data_and_rank_run_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            self._seed_clear_data(db_path)

            result = clear_data(db_path=db_path, scope="all")

            self.assertEqual(result.explored, 1)
            self.assertEqual(result.offers, 1)
            self.assertEqual(result.rankings, 1)
            self.assertEqual(result.ranking_runs, 1)
            self.assertEqual(list_explored_offers(db_path), [])
            self.assertEqual(list_unranked_review_offers(db_path=db_path), [])
            self.assertEqual(list_ranked_offers(db_path=db_path), [])
            self.assertEqual(get_clear_plan(db_path=db_path, scope="all").ranking_runs, 0)

    def test_clear_scope_rejects_unknown_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"

            with self.assertRaises(ValueError):
                clear_data(db_path=db_path, scope="unknown")

    def test_init_db_migrates_legacy_ai_reviews_to_preset_aware_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE offers (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source TEXT NOT NULL,
                        source_id TEXT,
                        url TEXT NOT NULL UNIQUE,
                        title TEXT NOT NULL,
                        company TEXT NOT NULL,
                        location TEXT,
                        description TEXT NOT NULL DEFAULT '',
                        published_at TEXT,
                        first_seen_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        last_fetched_at TEXT NOT NULL,
                        raw_json TEXT NOT NULL
                    );
                    CREATE TABLE ai_reviews (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        screening_result_id INTEGER,
                        offer_id INTEGER NOT NULL,
                        provider TEXT,
                        model TEXT,
                        profile_id TEXT NOT NULL,
                        profile_path TEXT NOT NULL DEFAULT 'profiles/default.json',
                        score INTEGER NOT NULL,
                        recommendation TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        review_json TEXT NOT NULL,
                        reviewed_at TEXT NOT NULL
                    );
                    INSERT INTO offers (
                        source, url, title, company, description,
                        first_seen_at, last_seen_at, last_fetched_at, raw_json
                    )
                    VALUES (
                        'test', 'https://example.com/legacy', 'Legacy', 'Example', 'systems',
                        '2026-05-24T12:00:00', '2026-05-24T12:00:00', '2026-05-24T12:00:00', '{}'
                    );
                    INSERT INTO ai_reviews (
                        offer_id, provider, model, profile_id, score,
                        recommendation, summary, review_json, reviewed_at
                    )
                    VALUES (
                        1, 'mock', 'mock-model', 'default', 70,
                        'medium', 'legacy review', '{}', '2026-05-24T12:01:00'
                    );
                    """
                )
            finally:
                connection.close()

            init_db(db_path)

            connection = sqlite3.connect(db_path)
            try:
                ai_columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(ai_reviews);").fetchall()
                }
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table';"
                    ).fetchall()
                }
                preset_id = connection.execute(
                    "SELECT preset_id FROM ai_reviews WHERE offer_id = 1;"
                ).fetchone()[0]
                backfilled = connection.execute(
                    "SELECT ai_score, verdict FROM offer_ai_reviews WHERE offer_id = 1 AND preset_id = 'balanced';"
                ).fetchone()
            finally:
                connection.close()

            self.assertIn("preset_id", ai_columns)
            self.assertIn("scoring_presets", tables)
            self.assertIn("offer_scores", tables)
            self.assertIn("offer_ai_reviews", tables)
            self.assertEqual(preset_id, "balanced")
            self.assertEqual(backfilled, (70, "medium"))

    def test_pruning_removes_oldest_unmarked_unranked_offers_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            for index in range(3):
                upsert_offers(
                    [_job(f"https://example.com/unranked-{index}", f"Unranked {index}")],
                    db_path=db_path,
                    fetched_at=f"2026-05-24T12:0{index}:00",
                )

            result = prune_storage(
                db_path,
                explored_capacity=100,
                unranked_capacity=2,
                ranked_capacity=100,
            )

            remaining = {offer["title"] for offer in list_unranked_review_offers(db_path=db_path)}
            self.assertEqual(result.deleted_unranked, 1)
            self.assertEqual(remaining, {"Unranked 1", "Unranked 2"})

    def test_pruning_preserves_marked_offers_while_unmarked_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            upsert_offers(
                [_job("https://example.com/marked", "Marked")],
                db_path=db_path,
                fetched_at="2026-05-24T12:00:00",
            )
            upsert_offers(
                [_job("https://example.com/unmarked", "Unmarked")],
                db_path=db_path,
                fetched_at="2026-05-24T12:01:00",
            )
            update_offer_status(db_path=db_path, offer_id=1, status="saved")

            prune_storage(
                db_path,
                explored_capacity=100,
                unranked_capacity=1,
                ranked_capacity=100,
            )

            remaining = list_unranked_review_offers(db_path=db_path)
            self.assertEqual([offer["title"] for offer in remaining], ["Marked"])

    def test_pruning_removes_unranked_before_ranked_when_ranked_capacity_allows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            upsert_offers(
                [
                    _job("https://example.com/unranked", "Unranked"),
                    _job("https://example.com/ranked", "Ranked"),
                ],
                db_path=db_path,
                fetched_at="2026-05-24T12:00:00",
            )
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
                offer_id=2,
                algorithm="rules",
                model=None,
                profile_id="default",
                score=50,
                recommendation="low",
                summary="summary",
                result=_result(None),
                ranked_at="2026-05-24T12:00:00",
            )

            result = prune_storage(
                db_path,
                explored_capacity=100,
                unranked_capacity=0,
                ranked_capacity=1,
            )

            self.assertEqual(result.deleted_unranked, 1)
            self.assertEqual(result.deleted_ranked, 0)
            self.assertEqual(get_storage_counts(db_path), result.after)
            self.assertEqual(result.after.unranked, 0)
            self.assertEqual(result.after.ranked, 1)

    def test_pruning_enforces_each_capacity_independently_and_keeps_rankings_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            for index in range(2):
                record_explored_job(
                    _source_job(f"explored-{index}", f"https://example.com/explored-{index}", f"Explored {index}"),
                    status="filtered_out",
                    db_path=db_path,
                    seen_at=f"2026-05-24T12:0{index}:00",
                )
            upsert_offers(
                [
                    _job("https://example.com/unranked-0", "Unranked 0"),
                    _job("https://example.com/unranked-1", "Unranked 1"),
                    _job("https://example.com/ranked-0", "Ranked 0"),
                    _job("https://example.com/ranked-1", "Ranked 1"),
                ],
                db_path=db_path,
                fetched_at="2026-05-24T12:00:00",
            )
            run_id = create_ranking_run(
                db_path=db_path,
                started_at="2026-05-24T12:00:00",
                algorithm="rules",
                model=None,
                profile_id="default",
                config={},
            )
            for offer_id, ranked_at in [(3, "2026-05-24T12:00:00"), (4, "2026-05-24T12:01:00")]:
                save_ranking(
                    db_path=db_path,
                    run_id=run_id,
                    offer_id=offer_id,
                    algorithm="rules",
                    model=None,
                    profile_id="default",
                    score=50,
                    recommendation="low",
                    summary="summary",
                    result=_result(None),
                    ranked_at=ranked_at,
                )

            result = prune_storage(
                db_path,
                explored_capacity=1,
                unranked_capacity=1,
                ranked_capacity=1,
            )

            self.assertEqual(result.deleted_explored, 1)
            self.assertEqual(result.deleted_unranked, 1)
            self.assertEqual(result.deleted_ranked, 1)
            self.assertEqual(result.after.explored, 1)
            self.assertEqual(result.after.unranked, 1)
            self.assertEqual(result.after.ranked, 1)
            self.assertEqual([offer["title"] for offer in list_ranked_offers(db_path=db_path)], ["Ranked 1"])

    def test_explored_pruning_preserves_keep_flag_while_unmarked_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            record_explored_job(
                _source_job("kept", "https://example.com/kept", "Kept"),
                status="filtered_out",
                keep_flag=True,
                db_path=db_path,
                seen_at="2026-05-24T12:00:00",
            )
            record_explored_job(
                _source_job("unkept", "https://example.com/unkept", "Unkept"),
                status="filtered_out",
                db_path=db_path,
                seen_at="2026-05-24T12:01:00",
            )

            prune_storage(
                db_path,
                explored_capacity=1,
                unranked_capacity=100,
                ranked_capacity=100,
            )

            records = list_explored_offers(db_path)
            self.assertEqual(records[0]["external_id"], "kept")
            self.assertEqual(records[0]["keep_flag"], 1)

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

