from __future__ import annotations

import tempfile
import unittest
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from app.models.job import JobOffer
from app.models.profile import CandidateProfile
from app.filtering.rules import RuleScoringConfig, evaluate_job
from app.storage.connection import init_db
from app.storage.exploration import (
    list_explored_offers,
    record_explored_job,
    save_exploration_metadata,
)
from app.storage.maintenance import clear_data, get_clear_plan, get_storage_counts, prune_storage
from app.storage.offers import exclude_existing_offers, update_offer_status, upsert_offers
from app.storage.reviews import (
    clear_rankings,
    create_ranking_run,
    list_ai_reviews,
    list_ranked_offers,
    list_screening_results,
    list_unranked_review_offers,
    save_ranking,
)
from app.workflows import WorkflowCancelled, _exploration_scope_key, _exploration_scope_payload
from app.workflows import fetch_offers, rank_offers


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


def _source_job(source_id: str | None, url: str, title: str) -> JobOffer:
    return JobOffer(
        source="test",
        source_id=source_id,
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
    def _write_profile(
        self,
        path: Path,
        *,
        positive_signals: dict[str, int] | None = None,
        negative_signals: dict[str, int] | None = None,
        threshold: int = 40,
    ) -> None:
        path.write_text(
            json.dumps(
                {
                    "name": "Test profile",
                    "positive_signals": positive_signals or {},
                    "negative_signals": negative_signals or {},
                    "screening_threshold": threshold,
                }
            ),
            encoding="utf-8",
        )

    def _seed_clear_data(self, db_path: Path) -> int:
        record_explored_job(
            _source_job("explored", "https://example.com/explored", "Explored"),
            status="filtered_out",
            db_path=db_path,
        )
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
        return run_id

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
                        profile_path TEXT NOT NULL,
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
                        offer_id, provider, model, profile_path, score,
                        recommendation, summary, review_json, reviewed_at
                    )
                    VALUES (
                        1, 'mock', 'mock-model', 'profiles/default.json', 70,
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
                profile_path="profiles/default.json",
                config={},
            )
            save_ranking(
                db_path=db_path,
                run_id=run_id,
                offer_id=2,
                algorithm="rules",
                model=None,
                profile_path="profiles/default.json",
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
                profile_path="profiles/default.json",
                config={},
            )
            for offer_id, ranked_at in [(3, "2026-05-24T12:00:00"), (4, "2026-05-24T12:01:00")]:
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

            with patch("app.workflows.fetch_arbeitnow", return_value=[existing, new]):
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

            with patch("app.workflows.fetch_arbeitnow", side_effect=[seen_jobs, [new]]) as fetch_mock:
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

            with patch("app.workflows.fetch_arbeitnow", side_effect=[[seen], [new]]):
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

            with patch("app.workflows.fetch_arbeitnow", side_effect=[[first], [second]]):
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

            with patch("app.workflows.fetch_arbeitnow", side_effect=[[filtered, inserted], [extra]]) as fetch_mock:
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

            with patch("app.workflows.fetch_arbeitnow", side_effect=[[first], [second]]):
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

            with patch("app.workflows.fetch_arbeitnow", side_effect=[[seen_one], [seen_two], [new]]):
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

            with patch("app.workflows.fetch_arbeitnow", return_value=[filtered]):
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

    def _save_fetch_scope_metadata(
        self,
        *,
        db_path: Path,
        newest_id: str,
        oldest_id: str,
        last_explored_page: int,
        profile_path: Path = Path("profiles/default.json"),
    ) -> None:
        scope = _exploration_scope_payload(
            source="arbeitnow",
            query="c++ simulation",
            country="fr",
            where=None,
            profile_path=profile_path,
            min_score=0,
        )
        save_exploration_metadata(
            db_path=db_path,
            scope_key=_exploration_scope_key(scope),
            source="arbeitnow",
            scope=scope,
            newest_id=newest_id,
            oldest_id=oldest_id,
            last_explored_page=last_explored_page,
        )

    def test_fast_backfill_falls_back_when_metadata_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite"
            new = _source_job("new", "https://example.com/new", "C++ Simulation")

            with patch("app.workflows.fetch_arbeitnow", return_value=[new]):
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

            with patch("app.workflows.fetch_arbeitnow", side_effect=[[new_top, previous_newest], [previous_oldest]]):
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

            with patch("app.workflows.fetch_arbeitnow", side_effect=[[previous_newest], []]) as fetch_mock:
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

            with patch("app.workflows.fetch_arbeitnow", side_effect=[[previous_newest], [skipped], [previous_oldest, older]]):
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

            with patch("app.workflows.fetch_arbeitnow", side_effect=[[previous_newest], [previous_oldest, already_seen]]):
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    min_score=0,
                    pages=2,
                    exploration_mode="fast_backfill",
                )

            self.assertEqual(result.stats.already_seen, 1)
            self.assertEqual(result.stats.inserted, 0)

    def test_fetch_workflow_persists_profile_driven_screening_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            db_path = base_path / "jobs.sqlite"
            profile_path = base_path / "profile.json"
            self._write_profile(profile_path, positive_signals={"simulation": 50}, threshold=40)
            matching = _source_job("source-1", "https://example.com/match", "Simulation Engineer")

            with patch("app.workflows.fetch_arbeitnow", return_value=[matching]):
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    profile_path=profile_path,
                    new_offers=1,
                    max_pages=1,
                )

            screenings = list_screening_results(db_path)
            self.assertEqual(result.stats.inserted, 1)
            self.assertEqual(len(screenings), 1)
            self.assertEqual(screenings[0]["title"], "Simulation Engineer")
            self.assertEqual(screenings[0]["profile_path"], str(profile_path))
            self.assertEqual(screenings[0]["passed"], 1)
            self.assertTrue(any("Fetch timing:" in message for message in result.messages))

    def test_fetch_workflow_uses_profile_signals_not_hardcoded_terms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            db_path = base_path / "jobs.sqlite"
            profile_path = base_path / "profile.json"
            self._write_profile(profile_path, positive_signals={"python": 50}, threshold=40)
            old_global_match = _source_job("source-1", "https://example.com/cpp", "C++ Simulation Engineer")

            with patch("app.workflows.fetch_arbeitnow", return_value=[old_global_match]):
                result = fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    profile_path=profile_path,
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
            profile_path = base_path / "profile.json"
            profile_path.write_text(
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

            with patch("app.workflows.fetch_adzuna", side_effect=[[first], [second]]) as fetch_mock:
                result = fetch_offers(
                    source="adzuna",
                    db_path=db_path,
                    profile_path=profile_path,
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
            self.assertTrue(any("Using 2 profile search requests" in message for message in result.messages))

    def test_adzuna_manual_query_overrides_profile_search_queries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            db_path = base_path / "jobs.sqlite"
            profile_path = base_path / "profile.json"
            profile_path.write_text(
                json.dumps({"search_queries": {"en": ["systems engineer"]}, "screening_threshold": 0}),
                encoding="utf-8",
            )
            offer = _source_job("manual", "https://example.com/manual", "Manual Query Engineer")

            with patch("app.workflows.fetch_adzuna", return_value=[offer]) as fetch_mock:
                result = fetch_offers(
                    source="adzuna",
                    db_path=db_path,
                    profile_path=profile_path,
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
            profile_path = base_path / "profile.json"
            profile_path.write_text(
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

            with patch("app.workflows.fetch_adzuna", side_effect=[[duplicate], [duplicate]]):
                result = fetch_offers(
                    source="adzuna",
                    db_path=db_path,
                    profile_path=profile_path,
                    query="",
                    country="fr",
                    new_offers=2,
                    max_pages=1,
                    min_score=0,
                )

            self.assertEqual(result.stats.inserted, 1)
            self.assertEqual(result.stats.already_seen, 1)
            self.assertEqual(len(list_explored_offers(db_path)), 1)

    def test_ai_review_persistence_references_screening_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            db_path = base_path / "jobs.sqlite"
            profile_path = base_path / "profile.json"
            self._write_profile(profile_path, positive_signals={"simulation": 50}, threshold=40)
            matching = _source_job("source-1", "https://example.com/match", "Simulation Engineer")

            with patch("app.workflows.fetch_arbeitnow", return_value=[matching]):
                fetch_offers(
                    source="arbeitnow",
                    db_path=db_path,
                    profile_path=profile_path,
                    new_offers=1,
                    max_pages=1,
                )

            rank_offers(
                profile_path=profile_path,
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
