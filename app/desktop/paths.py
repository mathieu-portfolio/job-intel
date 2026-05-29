from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.runtime_paths import (
    APP_NAME,
    JOB_INTEL_DATA_DIR_ENV,
    JOB_INTEL_DB_PATH_ENV,
    RuntimePaths,
    apply_runtime_paths,
)

DEFAULT_DATABASE_FILENAME = "job_intel.sqlite"


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


def _settings_path_for_data_dir(data_dir: Path) -> Path:
    return data_dir / "settings.json"


def _read_settings(settings_path: Path) -> dict[str, Any]:
    if not settings_path.exists():
        return {}
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_database_path(value: str | Path, *, default_filename: str = DEFAULT_DATABASE_FILENAME) -> Path:
    """Return a concrete SQLite file path from a user-entered path.

    If the value looks like a folder, use the default database filename inside it.
    This makes future folder-picker integrations easy: a picker can return a
    directory while advanced users can still type a full .sqlite/.db path.
    """

    raw = Path(value).expanduser()
    if raw.exists() and raw.is_dir():
        return raw / default_filename
    if raw.suffix:
        return raw
    return raw / default_filename


def _write_runtime_settings(paths: RuntimePaths) -> None:
    paths.settings_path.parent.mkdir(parents=True, exist_ok=True)
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


def get_desktop_runtime_paths(app_name: str = APP_NAME) -> RuntimePaths:
    data_dir = get_app_data_dir(app_name)
    settings_path = _settings_path_for_data_dir(data_dir)
    settings = _read_settings(settings_path)

    db_override = os.environ.get(JOB_INTEL_DB_PATH_ENV, "").strip()
    if db_override:
        db_path = _normalize_database_path(db_override)
    else:
        stored_db_path = str(settings.get("db_path") or "").strip()
        db_path = _normalize_database_path(stored_db_path) if stored_db_path else data_dir / DEFAULT_DATABASE_FILENAME

    return RuntimePaths(
        data_dir=data_dir,
        db_path=db_path,
        profiles_dir=data_dir / "profiles",
        scoring_presets_dir=data_dir / "config" / "scoring_presets",
        settings_path=settings_path,
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
    _write_runtime_settings(paths)
    apply_runtime_paths(paths)
    return paths


def ensure_desktop_data_dir(app_name: str = APP_NAME) -> Path:
    return ensure_desktop_runtime_paths(app_name).data_dir


def set_desktop_database_path(
    runtime_paths: RuntimePaths,
    requested_path: str | Path,
    *,
    move_existing: bool = True,
) -> RuntimePaths:
    """Persist and apply a new desktop database file path.

    The data directory remains the OS default. Only the SQLite file is moved,
    because it is the part that can grow large.
    """

    new_db_path = _normalize_database_path(requested_path)
    new_db_path.parent.mkdir(parents=True, exist_ok=True)

    current_db_path = runtime_paths.db_path
    try:
        same_path = current_db_path.resolve(strict=False) == new_db_path.resolve(strict=False)
    except OSError:
        same_path = current_db_path == new_db_path
    if same_path:
        _write_runtime_settings(runtime_paths)
        apply_runtime_paths(runtime_paths)
        return runtime_paths

    if new_db_path.exists():
        raise FileExistsError(f"A file already exists at {new_db_path}.")

    if move_existing and current_db_path.exists():
        shutil.move(str(current_db_path), str(new_db_path))

    updated_paths = replace(runtime_paths, db_path=new_db_path)
    _write_runtime_settings(updated_paths)
    apply_runtime_paths(updated_paths)
    return updated_paths
