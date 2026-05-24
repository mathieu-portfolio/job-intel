from __future__ import annotations

import json
from pathlib import Path

from app.models.job import JobOffer
from app.models.profile import CandidateProfile


DATA_DIR = Path("data")
LATEST_NORMALIZED_PATH = DATA_DIR / "normalized" / "latest_jobs.json"


def save_jobs(jobs: list[JobOffer], path: Path = LATEST_NORMALIZED_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(
            [job.model_dump(mode="json") for job in jobs],
            file,
            indent=2,
            ensure_ascii=False,
        )


def load_jobs(path: Path = LATEST_NORMALIZED_PATH) -> list[JobOffer]:
    with path.open("r", encoding="utf-8") as file:
        raw_jobs = json.load(file)

    if not isinstance(raw_jobs, list):
        raise ValueError(f"Expected a JSON list of jobs in {path}")

    return [JobOffer.model_validate(job) for job in raw_jobs]


def load_profile(path: Path) -> CandidateProfile:
    with path.open("r", encoding="utf-8") as file:
        raw_profile = json.load(file)

    return CandidateProfile.model_validate(raw_profile)
