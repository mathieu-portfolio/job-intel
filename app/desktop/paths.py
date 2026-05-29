from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "JobIntel"


def get_app_data_dir(app_name: str = APP_NAME) -> Path:
    """Return the writable per-user data directory for this OS."""

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


def get_desktop_db_path(app_name: str = APP_NAME) -> Path:
    return get_app_data_dir(app_name) / "job_intel.sqlite"


def ensure_desktop_data_dir(app_name: str = APP_NAME) -> Path:
    data_dir = get_app_data_dir(app_name)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir
