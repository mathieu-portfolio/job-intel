from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.storage.files import default_profile_path, discover_profile_paths


ADZUNA_MARKETS: list[dict[str, str]] = [
    {"label": "France", "value": "fr"},
    {"label": "Germany", "value": "de"},
    {"label": "United Kingdom", "value": "gb"},
    {"label": "United States", "value": "us"},
    {"label": "Australia", "value": "au"},
    {"label": "Austria", "value": "at"},
    {"label": "Belgium", "value": "be"},
    {"label": "Brazil", "value": "br"},
    {"label": "Canada", "value": "ca"},
    {"label": "India", "value": "in"},
    {"label": "Italy", "value": "it"},
    {"label": "Netherlands", "value": "nl"},
    {"label": "New Zealand", "value": "nz"},
    {"label": "Poland", "value": "pl"},
    {"label": "Singapore", "value": "sg"},
    {"label": "South Africa", "value": "za"},
    {"label": "Spain", "value": "es"},
]


def _readable_label(path: Path) -> str:
    label = path.stem.replace("_", " ").replace("-", " ").replace(".", " ").strip()
    return label.title() if label else path.name


def _profile_label(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8") as file:
            raw_profile: dict[str, Any] = json.load(file)
    except (OSError, json.JSONDecodeError):
        return _readable_label(path)
    return str(raw_profile.get("name") or raw_profile.get("id") or _readable_label(path))


def discover_profiles(profiles_dir: Path = Path("profiles")) -> list[dict[str, str]]:
    return [
        {"label": _profile_label(path), "value": str(path).replace("\\", "/")}
        for path in discover_profile_paths(profiles_dir)
    ]


def get_default_profile_value(profiles_dir: Path = Path("profiles")) -> str:
    return str(default_profile_path(profiles_dir)).replace("\\", "/")


def _discover_json_options(directory: Path) -> list[dict[str, str]]:
    if not directory.exists():
        return []
    return [
        {
            "label": _readable_label(path),
            "value": str(path).replace("\\", "/"),
        }
        for path in sorted(directory.glob("*.json"))
    ]
