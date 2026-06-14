from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.filtering.presets import ScoringPresetFile
from app.filtering.rule_config import RuleScoringConfig
from app.runtime_paths import get_profiles_dir, get_scoring_preset_dir
from app.models.profile import CandidateProfile
from app.storage.files import load_profile
from app.storage.scoring import get_scoring_preset


def _profile_payload(profile_path: str) -> dict[str, object]:
    path = Path(profile_path)
    profile = load_profile(path)
    raw = profile.model_dump(mode="json", exclude_none=True)
    return {
        "path": str(path).replace("\\", "/"),
        "profile": raw,
        "payload_json": json.dumps(raw, ensure_ascii=False),
    }


def _write_profile(path: Path, payload: dict[str, object]) -> None:
    profile = load_profile(path).model_copy(update=CandidateProfile.model_validate(payload).model_dump())
    normalized = profile.model_dump(mode="json", exclude_none=True)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")



def _preset_file_path(preset_id: str) -> Path:
    safe_id = preset_id.strip()
    if not safe_id or "/" in safe_id or "\\" in safe_id or safe_id in {".", ".."}:
        raise ValueError("Invalid preset id.")
    return get_scoring_preset_dir() / f"{safe_id}.json"


def _preset_payload(preset_id: str) -> dict[str, object]:
    preset = get_scoring_preset(preset_id)
    raw = {
        "id": preset.id,
        "name": preset.name,
        "description": preset.description,
        "order": preset.order,
        "is_builtin": preset.is_builtin,
        "enabled": preset.enabled,
        "weights": preset.weights.model_dump(mode="json"),
    }
    return {
        "preset": raw,
        "payload_json": json.dumps(raw, ensure_ascii=False),
    }




def _empty_profile_payload(profile_id: str = "new_profile") -> dict[str, object]:
    raw = CandidateProfile(profile_id=profile_id, name=profile_id.replace("_", " ").title()).model_dump(mode="json", exclude_none=True)
    path = get_profiles_dir() / f"{profile_id}.json"
    return {
        "path": str(path).replace("\\", "/"),
        "profile": raw,
        "payload_json": json.dumps(raw, ensure_ascii=False),
    }


def _empty_preset_payload(preset_id: str = "new_preset") -> dict[str, object]:
    raw = {
        "id": preset_id,
        "name": preset_id.replace("_", " ").title(),
        "description": "Created from an empty configuration.",
        "order": 100,
        "is_builtin": False,
        "enabled": True,
        "weights": _default_preset_weights().model_dump(mode="json"),
    }
    return {
        "preset": raw,
        "payload_json": json.dumps(raw, ensure_ascii=False),
    }

def _write_preset(original_id: str, payload: dict[str, object]) -> Path:
    preset_file = ScoringPresetFile.model_validate(payload)
    normalized = preset_file.model_dump(mode="json", exclude_none=True)
    get_scoring_preset_dir().mkdir(parents=True, exist_ok=True)
    path = _preset_file_path(preset_file.id)
    old_path = _preset_file_path(original_id)
    if old_path != path and old_path.exists():
        old_path.unlink()
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _safe_config_id(value: str) -> str:
    cleaned = value.strip().lower().replace(" ", "_").replace("-", "_")
    if not cleaned or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in cleaned):
        raise ValueError("Use only lowercase letters, numbers, and underscores for the ID.")
    return cleaned


def _profile_file_path(profile_id: str) -> Path:
    return get_profiles_dir() / f"{_safe_config_id(profile_id)}.json"


def _write_new_profile(profile_id: str, source_profile: str, name: str | None) -> Path:
    safe_id = _safe_config_id(profile_id)
    path = _profile_file_path(safe_id)
    if path.exists():
        raise ValueError(f"Profile already exists: {path}")
    source = Path(source_profile) if source_profile else get_profiles_dir() / "default.json"
    if source.exists():
        payload = load_profile(source).model_dump(mode="json", exclude_none=True)
    else:
        payload = CandidateProfile(profile_id=safe_id).model_dump(mode="json", exclude_none=True)
    payload["profile_id"] = safe_id
    if name and name.strip():
        payload["name"] = name.strip()
    elif not payload.get("name"):
        payload["name"] = safe_id.replace("_", " ").title()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _default_preset_weights() -> RuleScoringConfig:
    return RuleScoringConfig(
        category_weights={},
        cumulative_categories=set(),
        exclusive_categories={"location", "location_preferences"},
        no_signal_score=50,
        positive_score_scale=12.0,
        negative_score_scale=12.0,
        strong_negative_threshold=-0.4,
        strong_negative_score_cap=35,
    )


def _write_new_preset(preset_id: str, source_preset: str, name: str | None, db_path: Path) -> Path:
    safe_id = _safe_config_id(preset_id)
    path = _preset_file_path(safe_id)
    if path.exists():
        raise ValueError(f"Preset already exists: {path}")
    try:
        source = get_scoring_preset(source_preset or "balanced", db_path=db_path)
        description = source.description
        order = source.order + 1
        weights = source.weights
    except ValueError:
        description = "Created from an empty configuration."
        order = 100
        weights = _default_preset_weights()
    payload = {
        "id": safe_id,
        "name": name.strip() if name and name.strip() else safe_id.replace("_", " ").title(),
        "description": description,
        "order": order,
        "is_builtin": False,
        "enabled": True,
        "weights": weights.model_dump(mode="json"),
    }
    get_scoring_preset_dir().mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _config_export_paths() -> list[Path]:
    roots = [get_profiles_dir(), get_scoring_preset_dir()]
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(path for path in root.rglob("*.json") if path.is_file())
    return sorted(files)


def _config_archive_name(path: Path) -> str:
    profile_root = get_profiles_dir()
    try:
        return path.relative_to(profile_root.parent).as_posix()
    except ValueError:
        pass
    try:
        return (Path("config") / "scoring_presets" / path.name).as_posix()
    except ValueError:
        return path.name



def _profile_export_paths() -> list[Path]:
    root = get_profiles_dir()
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.json") if path.is_file())


def _preset_export_paths() -> list[Path]:
    preset_dir = get_scoring_preset_dir()
    if not preset_dir.exists():
        return []
    return sorted(path for path in preset_dir.rglob("*.json") if path.is_file())


def _export_json_zip(files: list[Path], filename: str) -> FileResponse:
    if not files:
        raise HTTPException(status_code=404, detail="No JSON files found to export.")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        archive_path = Path(tmp.name)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, _config_archive_name(path))
    return FileResponse(archive_path, media_type="application/zip", filename=filename, background=None)


def _import_config_zip_kind(path: Path, expected_kind: str) -> dict[str, int]:
    counts = {"profiles": 0, "presets": 0, "skipped": 0}
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            destination = _safe_import_member(member.filename)
            if destination is None:
                counts["skipped"] += 1
                continue
            kind, output_path = destination
            if kind != expected_kind:
                counts["skipped"] += 1
                continue
            raw = archive.read(member)
            try:
                payload = json.loads(raw.decode("utf-8"))
                if kind == "profile":
                    CandidateProfile.model_validate(payload)
                    counts["profiles"] += 1
                else:
                    ScoringPresetFile.model_validate(payload)
                    counts["presets"] += 1
            except Exception as error:
                raise ValueError(f"Invalid {kind} file in archive: {member.filename}: {error}") from error
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(raw)
    return counts

def _validate_sqlite_file(path: Path) -> None:
    with path.open("rb") as file:
        header = file.read(16)
    if header != b"SQLite format 3\x00":
        raise ValueError("The uploaded file does not look like a SQLite database.")


def _safe_import_member(name: str) -> tuple[str, Path] | None:
    normalized = name.replace("\\", "/").lstrip("/")
    target = Path(normalized)
    if ".." in target.parts or target.suffix.lower() != ".json":
        return None
    if len(target.parts) == 2 and target.parts[0] == "profiles":
        return "profile", get_profiles_dir() / target.name
    if len(target.parts) == 3 and target.parts[0] == "config" and target.parts[1] == "scoring_presets":
        return "preset", get_scoring_preset_dir() / target.name
    return None


def _import_config_zip(path: Path) -> dict[str, int]:
    counts = {"profiles": 0, "presets": 0, "skipped": 0}
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            destination = _safe_import_member(member.filename)
            if destination is None:
                counts["skipped"] += 1
                continue
            kind, output_path = destination
            raw = archive.read(member)
            try:
                payload = json.loads(raw.decode("utf-8"))
                if kind == "profile":
                    CandidateProfile.model_validate(payload)
                    counts["profiles"] += 1
                else:
                    ScoringPresetFile.model_validate(payload)
                    counts["presets"] += 1
            except Exception as error:
                raise ValueError(f"Invalid {kind} file in archive: {member.filename}: {error}") from error
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(raw)
    return counts


__all__ = [
    "_profile_payload",
    "_write_profile",
    "_preset_file_path",
    "_preset_payload",
    "_write_preset",
    "_safe_config_id",
    "_profile_file_path",
    "_write_new_profile",
    "_write_new_preset",
    "_config_export_paths",
    "_config_archive_name",
    "_profile_export_paths",
    "_preset_export_paths",
    "_export_json_zip",
    "_import_config_zip_kind",
    "_validate_sqlite_file",
    "_safe_import_member",
    "_import_config_zip",
]
