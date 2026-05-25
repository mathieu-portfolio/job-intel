from __future__ import annotations

from pathlib import Path


def _readable_label(path: Path) -> str:
    label = path.stem.replace("_", " ").replace("-", " ").replace(".", " ").strip()
    return label.title() if label else path.name


def discover_profiles(profiles_dir: Path = Path("profiles")) -> list[dict[str, str]]:
    profiles = _discover_json_options(profiles_dir)
    default_value = str((profiles_dir / "default.json")).replace("\\", "/")
    if any(profile["value"] == default_value for profile in profiles):
        profiles.sort(key=lambda profile: (profile["value"] != default_value, profile["label"]))
    return profiles or [{"label": "Default", "value": default_value}]


def discover_weight_files(config_dir: Path = Path("config")) -> list[dict[str, str]]:
    return [
        {"label": "Default weights", "value": ""},
        *_discover_json_options(config_dir),
    ]


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
