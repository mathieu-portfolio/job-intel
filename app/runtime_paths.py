from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.resources import APP_NAME, resource_path

JOB_INTEL_DATA_DIR_ENV = "JOB_INTEL_DATA_DIR"
JOB_INTEL_DB_PATH_ENV = "JOB_INTEL_DB_PATH"
JOB_INTEL_PROFILES_DIR_ENV = "JOB_INTEL_PROFILES_DIR"
JOB_INTEL_SCORING_PRESET_DIR_ENV = "JOB_INTEL_SCORING_PRESET_DIR"

DEFAULT_PROFILES_DIR = resource_path("profiles")
DEFAULT_SCORING_PRESET_DIR = resource_path("config", "scoring_presets")


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
    os.environ[JOB_INTEL_DB_PATH_ENV] = str(paths.db_path)
    os.environ[JOB_INTEL_PROFILES_DIR_ENV] = str(paths.profiles_dir)
    os.environ[JOB_INTEL_SCORING_PRESET_DIR_ENV] = str(paths.scoring_presets_dir)
