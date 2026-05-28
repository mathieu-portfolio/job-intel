from __future__ import annotations

from pathlib import Path

from fastapi import Request

from app.storage.files import profile_id_from_path
from app.ui_options import discover_profiles


def _active_profile_path(request: Request) -> str:
    profiles = discover_profiles()
    default = profiles[0]["value"] if profiles else "profiles/default.json"
    selected = (request.cookies.get("job_intel_active_profile") or default).strip()
    values = {profile["value"] for profile in profiles}
    return selected if selected in values else default


def _active_profile_context(request: Request) -> dict[str, str]:
    active_value = _active_profile_path(request)
    profiles = discover_profiles()
    active = next((profile for profile in profiles if profile["value"] == active_value), None)
    return {
        "value": active_value,
        "id": profile_id_from_path(active_value),
        "label": active["label"] if active else Path(active_value).stem.title(),
    }


def _common_template_context(request: Request) -> dict[str, object]:
    return {
        "profiles": discover_profiles(),
        "active_profile": _active_profile_context(request),
    }


def _safe_local_path(value: str | None, default: str = "/") -> str:
    path = (value or default).strip()
    if not path.startswith("/") or path.startswith("//"):
        return default
    return path


__all__ = [
    "_active_profile_path",
    "_active_profile_context",
    "_common_template_context",
    "_safe_local_path",
]
