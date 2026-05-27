from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models.profile import CandidateProfile

DEFAULT_PROFILES_DIR = Path("profiles")


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

    profile_id = str(raw_profile.get("id") or path.stem)
    return (order_value, profile_id.lower(), path.name.lower())


def discover_profile_paths(profiles_dir: Path = DEFAULT_PROFILES_DIR) -> list[Path]:
    if not profiles_dir.exists():
        return []
    return sorted(profiles_dir.glob("*.json"), key=_profile_sort_key)


def default_profile_path(profiles_dir: Path = DEFAULT_PROFILES_DIR) -> Path:
    profiles = discover_profile_paths(profiles_dir)
    if not profiles:
        raise FileNotFoundError(f"No profile JSON files found in {profiles_dir}.")
    return profiles[0]


def resolve_profile_path(path: Path | str | None, profiles_dir: Path = DEFAULT_PROFILES_DIR) -> Path:
    if path is None or str(path).strip() == "":
        return default_profile_path(profiles_dir)
    return Path(path)


def load_profile(path: Path | str | None = None) -> CandidateProfile:
    resolved_path = resolve_profile_path(path)
    with resolved_path.open("r", encoding="utf-8") as file:
        raw_profile = json.load(file)

    return CandidateProfile.model_validate(raw_profile)
