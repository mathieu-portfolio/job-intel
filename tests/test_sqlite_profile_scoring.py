from __future__ import annotations

from tests._sqlite_review_shared import *


class SqliteProfileScoringTests(BaseSqliteReviewTests):
    def test_same_offer_can_have_different_scores_per_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "jobs.sqlite"
            init_db(db_path)
            job = _job("https://example.com/profile-score", "Profile Score")
            upsert_offers([job], db_path=db_path)
            offer_id = find_existing_offer_id(job, db_path=db_path)
            self.assertIsNotNone(offer_id)

            save_offer_score(
                db_path=db_path,
                offer_id=int(offer_id),
                profile_id="profile_a",
                preset_id="balanced",
                evaluation=_evaluation(90),
            )
            save_offer_score(
                db_path=db_path,
                offer_id=int(offer_id),
                profile_id="profile_b",
                preset_id="balanced",
                evaluation=_evaluation(45),
            )

            profile_a = list_screened_offers(db_path=db_path, profile_id="profile_a", threshold=0)
            profile_b = list_screened_offers(db_path=db_path, profile_id="profile_b", threshold=0)

            self.assertEqual(len(profile_a), 1)
            self.assertEqual(len(profile_b), 1)

    def test_same_offer_can_have_different_ai_reviews_per_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "jobs.sqlite"
            init_db(db_path)
            job = _job("https://example.com/profile-review", "Profile Review")
            upsert_offers([job], db_path=db_path)
            offer_id = find_existing_offer_id(job, db_path=db_path)
            self.assertIsNotNone(offer_id)

            save_ai_review(
                db_path=db_path,
                screening_result_id=None,
                offer_id=int(offer_id),
                provider="mock",
                model="test",
                profile_id="profile_a",
                preset_id="balanced",
                score=88,
                recommendation="high",
                summary="profile A",
                result=_result({"summary": "profile A"}),
            )
            save_ai_review(
                db_path=db_path,
                screening_result_id=None,
                offer_id=int(offer_id),
                provider="mock",
                model="test",
                profile_id="profile_b",
                preset_id="balanced",
                score=42,
                recommendation="low",
                summary="profile B",
                result=_result({"summary": "profile B"}),
            )

            reviews = sorted(list_ai_reviews(db_path=db_path), key=lambda row: row["profile_id"])
            self.assertEqual([review["profile_id"] for review in reviews], ["profile_a", "profile_b"])
            self.assertEqual([review["score"] for review in reviews], [88, 42])

    def test_profile_actions_do_not_overwrite_other_profile_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "jobs.sqlite"
            init_db(db_path)
            job = _job("https://example.com/profile-isolation", "Profile Isolation")
            upsert_offers([job], db_path=db_path)
            offer_id = find_existing_offer_id(job, db_path=db_path)
            self.assertIsNotNone(offer_id)

            for profile_id, score in (("profile_a", 81), ("profile_b", 52)):
                run_id = create_ranking_run(
                    db_path=db_path,
                    started_at="2026-05-27T12:00:00",
                    algorithm="ai",
                    model="test",
                    profile_path=profile_id,
                    config={},
                )
                save_ranking(
                    db_path=db_path,
                    run_id=run_id,
                    offer_id=int(offer_id),
                    algorithm="ai",
                    model="test",
                    profile_path=profile_id,
                    score=score,
                    recommendation="high" if score >= 75 else "low",
                    summary=profile_id,
                    result=_result({"summary": profile_id}),
                )

            profile_a = list_ranked_offers(db_path=db_path, profile_id="profile_a")
            profile_b = list_ranked_offers(db_path=db_path, profile_id="profile_b")

            self.assertEqual(len(profile_a), 1)
            self.assertEqual(len(profile_b), 1)
            self.assertEqual(profile_a[0]["score"], 81)
            self.assertEqual(profile_b[0]["score"], 52)

    def test_profile_signal_categories_are_normalized_by_item_weight_totals(self) -> None:
        profile = CandidateProfile.model_validate(
            {
                "signals": {
                    "small": [{"term": "simulation", "weight": 1.0}],
                    "large": [
                        {"term": "simulation", "weight": 1.0},
                        {"term": "missing one", "weight": 1.0},
                        {"term": "missing two", "weight": 1.0},
                    ],
                    "negative": [{"term": "generic CRUD", "weight": 1.0}],
                }
            }
        )

        config = RuleScoringConfig(
            category_weights={"small": 0.25, "large": 0.25, "negative": -0.20},
            no_signal_score=20,
            positive_score_scale=80,
            negative_score_scale=80,
            strong_negative_threshold=-0.20,
            strong_negative_score_cap=10,
        )
        positive = evaluate_job(
            _job("https://example.com/sim", "Simulation Engineer"),
            profile=profile,
            config=config,
        )
        negative = evaluate_job(
            _job("https://example.com/crud", "Generic CRUD Engineer"),
            profile=profile,
            config=config,
        )

        self.assertGreater(positive.normalized_score, negative.normalized_score)
        self.assertTrue(any("small" in reason for reason in positive.reasoning))
        self.assertTrue(any("large" in reason for reason in positive.reasoning))

    def test_legacy_profile_fields_migrate_to_signal_categories(self) -> None:
        profile = CandidateProfile.model_validate(
            {
                "interests": ["simulation"],
                "positive_signals": {"python": 50},
                "negative_signals": {"sales": -30},
            }
        )

        self.assertIn("interests", profile.signals)
        self.assertIn("positive_signals", profile.signals)
        self.assertIn("negative_signals", profile.signals)
        self.assertEqual(profile.signals["interests"].items[0].term, "simulation")

    def test_fetch_workflow_honors_cancellation_before_loading_profile(self) -> None:
        with self.assertRaises(WorkflowCancelled):
            fetch_offers(cancelled=lambda: True)

    def test_rank_workflow_honors_cancellation_before_loading_profile(self) -> None:
        with self.assertRaises(WorkflowCancelled):
            rank_offers(cancelled=lambda: True)

    def test_profile_must_match_rejects_when_no_any_term_matches(self) -> None:
        profile = CandidateProfile.model_validate(
            {
                "must_match": {"any": ["simulation"]},
                "signals": {
                    "interests": [{"term": "python", "weight": 1.0}],
                },
            }
        )
        config = RuleScoringConfig(
            must_match={"any": []},
            category_weights={"interests": 0.50},
            no_signal_score=20,
            positive_score_scale=80,
            negative_score_scale=80,
            strong_negative_threshold=-0.20,
            strong_negative_score_cap=10,
        )

        evaluation = evaluate_job(
            _job("https://example.com/python", "Python Developer"),
            profile=profile,
            config=config,
        )

        self.assertEqual(evaluation.normalized_score, 0)
        self.assertEqual(evaluation.decision, "skip")
        self.assertTrue(any("must_match.any" in reason for reason in evaluation.reasoning))

    def test_preset_must_match_allows_scoring_when_any_term_matches(self) -> None:
        profile = CandidateProfile.model_validate(
            {
                "signals": {
                    "interests": [{"term": "simulation", "weight": 1.0}],
                },
            }
        )
        config = RuleScoringConfig(
            must_match={"any": ["simulation", "aerospace"]},
            category_weights={"interests": 0.50},
            no_signal_score=20,
            positive_score_scale=80,
            negative_score_scale=80,
            strong_negative_threshold=-0.20,
            strong_negative_score_cap=10,
        )

        evaluation = evaluate_job(
            _job("https://example.com/simulation", "Simulation Engineer"),
            profile=profile,
            config=config,
        )

        self.assertGreater(evaluation.normalized_score, 0)
        self.assertNotEqual(evaluation.decision, "skip")

    def test_fast_rule_scoring_matches_english_alias(self) -> None:
        profile = CandidateProfile.model_validate(
            {
                "signals": {
                    "interests": [
                        {
                            "term": "systems",
                            "weight": 1.0,
                            "aliases": {"en": ["system programming"], "fr": ["programmation systeme"]},
                        }
                    ]
                }
            }
        )
        config = RuleScoringConfig(
            category_weights={"interests": 0.50},
            no_signal_score=20,
            positive_score_scale=80,
            negative_score_scale=80,
            strong_negative_threshold=-0.20,
            strong_negative_score_cap=10,
        )

        evaluation = evaluate_job(
            JobOffer(
                source="test",
                title="Runtime Engineer",
                company="Example",
                url="https://example.com/runtime",
                description="Work on low-level system programming.",
                raw_json={},
            ),
            profile=profile,
            config=config,
        )

        match = evaluation.matched_positive_terms[0]
        self.assertEqual(match.category, "interests")
        self.assertEqual(match.term, "systems")
        self.assertEqual(match.matched_alias, "system programming")
        self.assertEqual(match.language, "en")
        self.assertGreater(evaluation.normalized_score, 20)

    def test_fast_rule_scoring_matches_french_alias(self) -> None:
        profile = CandidateProfile.model_validate(
            {
                "signals": {
                    "interests": [
                        {
                            "term": "systems",
                            "aliases": {"fr": ["programmation systeme"]},
                        }
                    ]
                }
            }
        )
        config = RuleScoringConfig(
            category_weights={"interests": 0.50},
            no_signal_score=20,
            positive_score_scale=80,
            negative_score_scale=80,
            strong_negative_threshold=-0.20,
            strong_negative_score_cap=10,
        )

        evaluation = evaluate_job(
            JobOffer(
                source="test",
                title="Ingenieur logiciel",
                company="Example",
                url="https://example.com/fr",
                description="Developpement en programmation systeme.",
                raw_json={},
            ),
            profile=profile,
            config=config,
        )

        self.assertEqual(evaluation.matched_positive_terms[0].matched_alias, "programmation systeme")
        self.assertEqual(evaluation.matched_positive_terms[0].language, "fr")

    def test_fast_rule_scoring_is_accent_insensitive(self) -> None:
        profile = CandidateProfile.model_validate(
            {
                "signals": {
                    "interests": [
                        {
                            "term": "systems",
                            "aliases": {"fr": ["programmation système"]},
                        }
                    ]
                }
            }
        )
        config = RuleScoringConfig(
            category_weights={"interests": 0.50},
            no_signal_score=20,
            positive_score_scale=80,
            negative_score_scale=80,
            strong_negative_threshold=-0.20,
            strong_negative_score_cap=10,
        )

        evaluation = evaluate_job(
            JobOffer(
                source="test",
                title="Ingenieur logiciel",
                company="Example",
                url="https://example.com/accent",
                description="Programmation systeme embarquee.",
                raw_json={},
            ),
            profile=profile,
            config=config,
        )

        self.assertEqual(evaluation.matched_positive_terms[0].matched_alias, "programmation système")

    def test_fast_rule_scoring_falls_back_to_canonical_term(self) -> None:
        profile = CandidateProfile.model_validate(
            {"signals": {"interests": [{"term": "simulation", "weight": 1.0}]}}
        )
        config = RuleScoringConfig(
            category_weights={"interests": 0.50},
            no_signal_score=20,
            positive_score_scale=80,
            negative_score_scale=80,
            strong_negative_threshold=-0.20,
            strong_negative_score_cap=10,
        )

        evaluation = evaluate_job(_job("https://example.com/sim", "Simulation Engineer"), profile=profile, config=config)

        self.assertEqual(evaluation.matched_positive_terms[0].term, "simulation")
        self.assertEqual(evaluation.matched_positive_terms[0].matched_alias, "simulation")

    def test_fast_rule_scoring_matches_disliked_work_negative_alias(self) -> None:
        profile = CandidateProfile.model_validate(
            {
                "signals": {
                    "disliked_work": [
                        {
                            "term": "generic CRUD",
                            "aliases": {"fr": ["formulaires metier"]},
                        }
                    ]
                }
            }
        )
        config = RuleScoringConfig(
            category_weights={"disliked_work": -0.50},
            no_signal_score=50,
            positive_score_scale=80,
            negative_score_scale=80,
            strong_negative_threshold=-0.20,
            strong_negative_score_cap=10,
        )

        evaluation = evaluate_job(
            JobOffer(
                source="test",
                title="Developpeur",
                company="Example",
                url="https://example.com/crud-fr",
                description="Maintenance de formulaires metier et back-office.",
                raw_json={},
            ),
            profile=profile,
            config=config,
        )

        match = evaluation.matched_negative_terms[0]
        self.assertEqual(match.category, "disliked_work")
        self.assertEqual(match.term, "generic CRUD")
        self.assertEqual(match.matched_alias, "formulaires metier")
        self.assertLess(evaluation.normalized_score, 50)

    def test_must_match_uses_aliases(self) -> None:
        profile = CandidateProfile.model_validate(
            {
                "must_match": {
                    "any": [
                        {
                            "term": "software",
                            "aliases": {"fr": ["logiciel"]},
                        }
                    ]
                },
                "signals": {"interests": [{"term": "simulation"}]},
            }
        )
        config = RuleScoringConfig(
            must_match={"any": []},
            category_weights={"interests": 0.50},
            no_signal_score=20,
            positive_score_scale=80,
            negative_score_scale=80,
            strong_negative_threshold=-0.20,
            strong_negative_score_cap=10,
        )

        evaluation = evaluate_job(
            JobOffer(
                source="test",
                title="Ingenieur logiciel simulation",
                company="Example",
                url="https://example.com/must-match-fr",
                description="Simulation numerique.",
                raw_json={},
            ),
            profile=profile,
            config=config,
        )

        self.assertNotEqual(evaluation.decision, "skip")
        self.assertGreater(evaluation.normalized_score, 0)

