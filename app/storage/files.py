from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.models.profile import CandidateProfile


@dataclass(frozen=True)
class ProfileInfo:
    profile_id: str
    label: str
    path: Path


def profile_id_from_path(path: Path | str) -> str:
    profile_path = Path(path)
    stem = profile_path.stem or "default"
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", stem).strip("_").lower()
    return cleaned or "default"


def load_profile(path: Path) -> CandidateProfile:
    with path.open("r", encoding="utf-8") as file:
        raw_profile = json.load(file)

    profile = CandidateProfile.model_validate(raw_profile)
    profile_id = profile.profile_id or profile_id_from_path(path)
    return profile.model_copy(update={"profile_id": profile_id})


def discover_profiles(profiles_dir: Path = Path("profiles")) -> list[ProfileInfo]:
    if not profiles_dir.exists():
        return [ProfileInfo(profile_id="default", label="Default", path=profiles_dir / "default.json")]
    profiles: list[ProfileInfo] = []
    for path in sorted(profiles_dir.glob("*.json")):
        try:
            profile = load_profile(path)
            label = profile.name or path.stem.replace("_", " ").replace("-", " ").title()
            profiles.append(ProfileInfo(profile_id=profile.profile_id or profile_id_from_path(path), label=label, path=path))
        except Exception:
            label = path.stem.replace("_", " ").replace("-", " ").title()
            profiles.append(ProfileInfo(profile_id=profile_id_from_path(path), label=label, path=path))
    default = profiles_dir / "default.json"
    profiles.sort(key=lambda item: (item.path != default, item.label.lower()))
    return profiles or [ProfileInfo(profile_id="default", label="Default", path=default)]
