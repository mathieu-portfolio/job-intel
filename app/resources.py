from __future__ import annotations

import os
import sys
from importlib import metadata
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
APP_NAME = "JobIntel"
_PACKAGE_DISTRIBUTION_NAME = "job-intel"


def is_frozen() -> bool:
    """Return True when running from a bundled executable."""

    return bool(getattr(sys, "frozen", False))


def bundled_root() -> Path:
    """Return the root directory containing bundled application resources."""

    if hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    return PROJECT_ROOT


def resource_path(*parts: str | os.PathLike[str]) -> Path:
    """Resolve a project resource in source mode or PyInstaller one-folder/one-file mode."""

    return bundled_root().joinpath(*parts)


def app_version() -> str:
    """Return the installed package version, with a stable fallback for source zips."""

    try:
        return metadata.version(_PACKAGE_DISTRIBUTION_NAME)
    except metadata.PackageNotFoundError:
        return "0.1.0"


def executable_path() -> Path:
    """Return the current executable path, useful for diagnostics."""

    return Path(sys.executable).resolve()
