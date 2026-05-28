from __future__ import annotations

from pathlib import Path

from app.filtering.rules import evaluate_job
from app.models.job import JobOffer
from app.models.profile import CandidateProfile
from app.storage.connection import init_db
from app.storage.maintenance import clear_data, get_storage_counts
from app.storage.offers import find_existing_offer_id, upsert_offers
from app.storage.reviews import create_ranking_run, list_ranked_offers, save_ranking
from app.storage.scoring import list_screened_offers, save_offer_score, save_screening_result


def _job(url: str, title: str, description: str = "C++ simulation systems") -> JobOffer:
    return JobOffer(
        source="test",
        title=title,
        company="Example",
        url=url,
        description=description,
        raw_json={},
    )


def test_screening_results_are_scoped_by_profile_path(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    profile_path = tmp_path / "profiles" / "cpp.json"
    profile_path.parent.mkdir()
    profile_path.write_text('{"profile_id":"cpp","name":"C++","signals":{"interests":{"items":[{"term":"C++","weight":1.0}]}},"screening_threshold":0}', encoding="utf-8")
    job = _job("https://example.com/cpp", "C++ Engineer")
    upsert_offers([job], db_path=db_path)
    offer_id = find_existing_offer_id(job, db_path=db_path)
    assert offer_id is not None

    evaluation = evaluate_job(job, profile=CandidateProfile.model_validate_json(profile_path.read_text(encoding="utf-8")))
    save_offer_score(db_path=db_path, offer_id=offer_id, preset_id="balanced", profile_id="cpp", evaluation=evaluation)
    save_screening_result(
        db_path=db_path,
        offer_id=offer_id,
        profile_path=str(profile_path),
        evaluation=evaluation,
        threshold=0,
    )

    rows = list_screened_offers(db_path=db_path, profile_id="cpp", threshold=0)
    assert [row["title"] for row in rows] == ["C++ Engineer"]
    assert rows[0]["fast_score"] >= 0


def test_clear_rankings_keeps_raw_offers(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    job = _job("https://example.com/reviewed", "Reviewed")
    upsert_offers([job], db_path=db_path)
    run_id = create_ranking_run(
        db_path=db_path,
        started_at="2026-05-28T12:00:00",
        algorithm="ai",
        model="mock",
        profile_path="profiles/default.json",
        config={},
    )
    save_ranking(
        db_path=db_path,
        run_id=run_id,
        offer_id=1,
        algorithm="ai",
        model="mock",
        profile_path="profiles/default.json",
        score=80,
        recommendation="high",
        summary="Good fit",
        result={"final_decision": {"final_score": 80, "recommendation": "high"}},
    )

    assert len(list_ranked_offers(db_path=db_path)) == 1
    result = clear_data(db_path=db_path, scope="rankings")

    assert result.rankings == 1
    assert len(list_ranked_offers(db_path=db_path)) == 0
    assert get_storage_counts(db_path).unranked == 1
