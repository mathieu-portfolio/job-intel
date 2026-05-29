from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "JobIntel"
JOB_INTEL_DATA_DIR_ENV = "JOB_INTEL_DATA_DIR"
JOB_INTEL_PROFILES_DIR_ENV = "JOB_INTEL_PROFILES_DIR"
JOB_INTEL_SCORING_PRESET_DIR_ENV = "JOB_INTEL_SCORING_PRESET_DIR"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILES_DIR = PROJECT_ROOT / "profiles"
DEFAULT_SCORING_PRESET_DIR = PROJECT_ROOT / "config" / "scoring_presets"


@dataclass(frozen=True)
class RuntimePaths:
    data_dir: Path
    db_path: Path
    profiles_dir: Path
    scoring_presets_dir: Path
    settings_path: Path


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else None


def get_profiles_dir() -> Path:
    # Keep source/CLI mode backward-compatible: relative profiles/ from CWD.
    # Desktop mode sets JOB_INTEL_PROFILES_DIR to an absolute user-data path.
    return _env_path(JOB_INTEL_PROFILES_DIR_ENV) or Path("profiles")


def get_scoring_preset_dir() -> Path:
    override = _env_path(JOB_INTEL_SCORING_PRESET_DIR_ENV)
    if override is not None:
        return override
    # Import lazily so tests and source mode can monkey-patch the historical
    # SCORING_PRESET_DIR constant. Desktop mode uses the env var above.
    try:
        from app.filtering import presets

        return presets.SCORING_PRESET_DIR
    except Exception:
        return DEFAULT_SCORING_PRESET_DIR


def apply_runtime_paths(paths: RuntimePaths) -> None:
    """Expose runtime paths to code paths that are not request-aware yet."""

    os.environ[JOB_INTEL_DATA_DIR_ENV] = str(paths.data_dir)
    os.environ[JOB_INTEL_PROFILES_DIR_ENV] = str(paths.profiles_dir)
    os.environ[JOB_INTEL_SCORING_PRESET_DIR_ENV] = str(paths.scoring_presets_dir)
