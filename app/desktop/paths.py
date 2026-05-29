from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from app.runtime_paths import APP_NAME, JOB_INTEL_DATA_DIR_ENV, RuntimePaths, apply_runtime_paths



def get_default_app_data_dir(app_name: str = APP_NAME) -> Path:
    """Return the default writable per-user data directory for this OS."""

    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / app_name
        return Path.home() / "AppData" / "Roaming" / app_name

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name

    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / app_name
    return Path.home() / ".local" / "share" / app_name


def get_app_data_dir(app_name: str = APP_NAME) -> Path:
    """Return the selected desktop data directory.

    JOB_INTEL_DATA_DIR is the migration seam for installers and future folder
    pickers. If it is not set, use the OS-recommended per-user location.
    """

    override = os.environ.get(JOB_INTEL_DATA_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return get_default_app_data_dir(app_name)


def get_desktop_runtime_paths(app_name: str = APP_NAME) -> RuntimePaths:
    data_dir = get_app_data_dir(app_name)
    return RuntimePaths(
        data_dir=data_dir,
        db_path=data_dir / "job_intel.sqlite",
        profiles_dir=data_dir / "profiles",
        scoring_presets_dir=data_dir / "config" / "scoring_presets",
        settings_path=data_dir / "settings.json",
    )


def get_desktop_db_path(app_name: str = APP_NAME) -> Path:
    return get_desktop_runtime_paths(app_name).db_path


def _copy_seed_jsons(source_dir: Path, target_dir: Path) -> None:
    if target_dir.exists() and any(target_dir.glob("*.json")):
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    if not source_dir.exists():
        return
    for source in sorted(source_dir.glob("*.json")):
        target = target_dir / source.name
        if not target.exists():
            shutil.copy2(source, target)


def ensure_desktop_runtime_paths(app_name: str = APP_NAME) -> RuntimePaths:
    from app.runtime_paths import DEFAULT_PROFILES_DIR, DEFAULT_SCORING_PRESET_DIR

    paths = get_desktop_runtime_paths(app_name)
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    paths.db_path.parent.mkdir(parents=True, exist_ok=True)
    _copy_seed_jsons(DEFAULT_PROFILES_DIR, paths.profiles_dir)
    _copy_seed_jsons(DEFAULT_SCORING_PRESET_DIR, paths.scoring_presets_dir)
    if not paths.settings_path.exists():
        paths.settings_path.write_text(
            json.dumps(
                {
                    "data_dir": str(paths.data_dir),
                    "db_path": str(paths.db_path),
                    "profiles_dir": str(paths.profiles_dir),
                    "scoring_presets_dir": str(paths.scoring_presets_dir),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    apply_runtime_paths(paths)
    return paths


def ensure_desktop_data_dir(app_name: str = APP_NAME) -> Path:
    return ensure_desktop_runtime_paths(app_name).data_dir
