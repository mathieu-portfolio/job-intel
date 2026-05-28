from __future__ import annotations

from tests._sqlite_review_shared import *


class AiReviewWorkflowTests(BaseSqliteReviewTests):
    def test_ai_review_persistence_references_screening_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            db_path = base_path / "jobs.sqlite"
            profile_id = base_path / "profile.json"
            self._write_profile(profile_id, positive_signals={"simulation": 50}, threshold=40)
            matching = _source_job("source-1", "https://example.com/match", "Simulation Engineer")

            with patch("app.workflow_parts.fetch.fetch_arbeitnow", return_value=[matching]):
                fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    profile_path=profile_id,
                    new_offers=1,
                    max_pages=1,
                )

            rank_offers(
                profile_path=profile_id,
                db_path=db_path,
                ranking_mode="ai",
                provider="mock",
                limit=1,
            )

            reviews = list_ai_reviews(db_path)
            self.assertEqual(len(reviews), 1)
            self.assertEqual(reviews[0]["title"], "Simulation Engineer")
            self.assertIsNotNone(reviews[0]["screening_result_id"])
            self.assertEqual(reviews[0]["provider"], "mock")

    def test_ai_ranking_concurrency_limit_is_respected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            db_path = base_path / "jobs.sqlite"
            profile_id = base_path / "profile.json"
            self._write_profile(profile_id, positive_signals={"systems": 50}, threshold=0)
            for index in range(4):
                self._seed_screened_offer(
                    db_path,
                    profile_id,
                    _source_job(f"ai-{index}", f"https://example.com/ai-{index}", f"AI Job {index}"),
                )
            active = 0
            max_active = 0
            lock = threading.Lock()

            def evaluate(*args, **kwargs) -> AiJobEvaluation:
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                try:
                    time.sleep(0.05)
                    return AiJobEvaluation(
                        fit_score=75,
                        technical_fit_score=75,
                        domain_fit_score=75,
                        role_interest_score=75,
                        learning_potential_score=75,
                        posting_quality_score=75,
                        portfolio_alignment_score=75,
                        summary="ok",
                        recommendation="high",
                    )
                finally:
                    with lock:
                        active -= 1

            with patch("app.workflow_parts.review.evaluate_job_with_ai", side_effect=evaluate):
                result = rank_offers(
                    profile_path=profile_id,
                    db_path=db_path,
                    ranking_mode="ai",
                    provider="mock",
                    limit=4,
                    ai_concurrency=2,
                )

            self.assertEqual(result.saved_count, 4)
            self.assertEqual(len(list_ai_reviews(db_path)), 4)
            self.assertLessEqual(max_active, 2)
            self.assertGreater(max_active, 1)

    def test_ai_ranking_parallel_reviews_are_faster_than_serial(self) -> None:
        def seed_case(base_path: Path) -> tuple[Path, Path]:
            db_path = base_path / "jobs.sqlite"
            profile_id = base_path / "profile.json"
            self._write_profile(profile_id, positive_signals={"systems": 50}, threshold=0)
            for index in range(4):
                self._seed_screened_offer(
                    db_path,
                    profile_id,
                    _source_job(f"ai-{index}", f"https://example.com/ai-{index}", f"AI Job {index}"),
                )
            return db_path, profile_id

        def evaluate(*args, **kwargs) -> AiJobEvaluation:
            time.sleep(0.5)
            return AiJobEvaluation(
                fit_score=75,
                technical_fit_score=75,
                domain_fit_score=75,
                role_interest_score=75,
                learning_potential_score=75,
                posting_quality_score=75,
                portfolio_alignment_score=75,
                summary="ok",
                recommendation="high",
            )

        with tempfile.TemporaryDirectory() as serial_dir, tempfile.TemporaryDirectory() as parallel_dir:
            serial_db, serial_profile = seed_case(Path(serial_dir))
            parallel_db, parallel_profile = seed_case(Path(parallel_dir))

            with patch("app.workflow_parts.review.evaluate_job_with_ai", side_effect=evaluate):
                started_at = time.perf_counter()
                serial_result = rank_offers(
                    profile_id=serial_profile,
                    db_path=serial_db,
                    ranking_mode="ai",
                    provider="mock",
                    limit=4,
                    ai_concurrency=1,
                )
                serial_elapsed = time.perf_counter() - started_at

            with patch("app.workflow_parts.review.evaluate_job_with_ai", side_effect=evaluate):
                started_at = time.perf_counter()
                parallel_result = rank_offers(
                    profile_id=parallel_profile,
                    db_path=parallel_db,
                    ranking_mode="ai",
                    provider="mock",
                    limit=4,
                    ai_concurrency=2,
                )
                parallel_elapsed = time.perf_counter() - started_at

            self.assertEqual(serial_result.saved_count, 4)
            self.assertEqual(parallel_result.saved_count, 4)
            self.assertLess(parallel_elapsed, serial_elapsed * 0.85)
            self.assertTrue(any("AI task 2/4 started" in message for message in parallel_result.messages))
            self.assertTrue(any("AI task" in message and "completed" in message for message in parallel_result.messages))

    def test_ai_ranking_failure_keeps_successful_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            db_path = base_path / "jobs.sqlite"
            profile_id = base_path / "profile.json"
            self._write_profile(profile_id, positive_signals={"systems": 50}, threshold=0)
            for title in ["Good One", "Fail Me", "Good Two"]:
                self._seed_screened_offer(
                    db_path,
                    profile_id,
                    _source_job(title.lower().replace(" ", "-"), f"https://example.com/{title}", title),
                )

            def evaluate(job: JobOffer, *args, **kwargs) -> AiJobEvaluation:
                if job.title == "Fail Me":
                    raise RuntimeError("model failed")
                return AiJobEvaluation(
                    fit_score=75,
                    technical_fit_score=75,
                    domain_fit_score=75,
                    role_interest_score=75,
                    learning_potential_score=75,
                    posting_quality_score=75,
                    portfolio_alignment_score=75,
                    summary=f"ok {job.title}",
                    recommendation="high",
                )

            with patch("app.workflow_parts.review.evaluate_job_with_ai", side_effect=evaluate):
                result = rank_offers(
                    profile_path=profile_id,
                    db_path=db_path,
                    ranking_mode="ai",
                    provider="mock",
                    limit=3,
                    ai_concurrency=2,
                )

            reviews = list_ai_reviews(db_path)
            self.assertEqual(result.saved_count, 2)
            self.assertEqual(result.skipped_count, 1)
            self.assertEqual(len(reviews), 2)
            self.assertTrue(any("AI evaluation failed" in message for message in result.messages))

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
                profile_id="default",
                config={},
            )
            ai_run = create_ranking_run(
                db_path=db_path,
                started_at="2026-05-24T12:00:01",
                algorithm="ai",
                model="mock",
                profile_id="default",
                config={},
            )
            hybrid_run = create_ranking_run(
                db_path=db_path,
                started_at="2026-05-24T12:00:02",
                algorithm="hybrid",
                model="mock",
                profile_id="default",
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
                    profile_id="default",
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
                profile_id="default",
                config={},
            )
            for offer_id in [1, 2]:
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

            offers = list_unranked_review_offers(db_path=db_path)
            self.assertEqual({offer["title"] for offer in offers}, {"Unranked Simulation", "Frontend"})

            filtered = list_unranked_review_offers(db_path=db_path, search="simulation")
            self.assertEqual([offer["title"] for offer in filtered], ["Unranked Simulation"])

    def test_rank_workflow_rules_mode_saves_summary_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            profile_id = Path(temp_dir) / "profile.json"
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

            result = rank_offers(
                profile_path=profile_id,
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

