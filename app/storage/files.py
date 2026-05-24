from __future__ import annotations

import json
from pathlib import Path

from app.models.job import JobOffer


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
