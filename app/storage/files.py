from __future__ import annotations

import json
from pathlib import Path

from app.models.profile import CandidateProfile


def load_profile(path: Path) -> CandidateProfile:
    with path.open("r", encoding="utf-8") as file:
        raw_profile = json.load(file)

    return CandidateProfile.model_validate(raw_profile)
