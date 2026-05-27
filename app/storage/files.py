from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass
from typing import Any

from app.models.profile import CandidateProfile

DEFAULT_PROFILES_DIR = Path("profiles")


@dataclass(frozen=True)
class ProfileInfo:
    profile_id: str
    label: str


def profile_id_from_value(value: Path | str | None, profiles_dir: Path = DEFAULT_PROFILES_DIR) -> str:
    if value is None or str(value).strip() == "":
        return default_profile_id(profiles_dir)
    raw = str(value).strip().replace("\\", "/")
    name = raw.rsplit("/", 1)[-1]
    return Path(name).stem


def profile_file(profile_id: str, profiles_dir: Path = DEFAULT_PROFILES_DIR) -> Path:
    return profiles_dir / f"{profile_id_from_value(profile_id, profiles_dir)}.json"


def _profile_sort_key(path: Path) -> tuple[float, str, str]:
    try:
        with path.open("r", encoding="utf-8") as file:
            raw_profile: dict[str, Any] = json.load(file)
    except (OSError, json.JSONDecodeError):
        raw_profile = {}

    order = raw_profile.get("order", 0)
    try:
        order_value = float(order)
    except (TypeError, ValueError):
        order_value = 0.0

    profile_id = path.stem
    return (order_value, profile_id.lower(), path.name.lower())


def discover_profile_ids(profiles_dir: Path = DEFAULT_PROFILES_DIR) -> list[str]:
    if not profiles_dir.exists():
        return []
    return [path.stem for path in sorted(profiles_dir.glob("*.json"), key=_profile_sort_key)]


def default_profile_id(profiles_dir: Path = DEFAULT_PROFILES_DIR) -> str:
    profiles = discover_profile_ids(profiles_dir)
    if not profiles:
        raise FileNotFoundError(f"No profile JSON files found in {profiles_dir}.")
    return profiles[0]


def discover_profiles(profiles_dir: Path = DEFAULT_PROFILES_DIR) -> list[ProfileInfo]:
    profiles: list[ProfileInfo] = []
    for profile_id in discover_profile_ids(profiles_dir):
        file_path = profile_file(profile_id, profiles_dir)
        try:
            with file_path.open("r", encoding="utf-8") as file:
                raw_profile: dict[str, Any] = json.load(file)
            label = str(raw_profile.get("name") or profile_id.replace("_", " ").replace("-", " ").title())
        except (OSError, json.JSONDecodeError):
            label = profile_id.replace("_", " ").replace("-", " ").title()
        profiles.append(ProfileInfo(profile_id=profile_id, label=label))
    return profiles


def load_profile(profile_id: Path | str | None = None, profiles_dir: Path = DEFAULT_PROFILES_DIR) -> CandidateProfile:
    selected_id = profile_id_from_value(profile_id, profiles_dir)
    candidate = Path(str(profile_id)) if profile_id is not None else profile_file(selected_id, profiles_dir)
    file_path = candidate if candidate.suffix and candidate.exists() else profile_file(selected_id, profiles_dir)
    with file_path.open("r", encoding="utf-8") as file:
        raw_profile = json.load(file)

    return CandidateProfile.model_validate(raw_profile)
