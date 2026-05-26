from __future__ import annotations

from pathlib import Path


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


def discover_profiles(profiles_dir: Path = Path("profiles")) -> list[dict[str, str]]:
    profiles = _discover_json_options(profiles_dir)
    default_value = str((profiles_dir / "default.json")).replace("\\", "/")
    if any(profile["value"] == default_value for profile in profiles):
        profiles.sort(key=lambda profile: (profile["value"] != default_value, profile["label"]))
    return profiles or [{"label": "Default", "value": default_value}]


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
