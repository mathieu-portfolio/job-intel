from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.filtering.presets import SCORING_PRESET_DIR, ScoringPresetFile
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
    return SCORING_PRESET_DIR / f"{safe_id}.json"


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


def _write_preset(original_id: str, payload: dict[str, object]) -> Path:
    preset_file = ScoringPresetFile.model_validate(payload)
    normalized = preset_file.model_dump(mode="json", exclude_none=True)
    SCORING_PRESET_DIR.mkdir(parents=True, exist_ok=True)
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
    return Path("profiles") / f"{_safe_config_id(profile_id)}.json"


def _write_new_profile(profile_id: str, source_profile: str, name: str | None) -> Path:
    path = _profile_file_path(profile_id)
    if path.exists():
        raise ValueError(f"Profile already exists: {path}")
    source = Path(source_profile or "profiles/default.json")
    payload = load_profile(source).model_dump(mode="json", exclude_none=True)
    if name and name.strip():
        payload["name"] = name.strip()
    elif not payload.get("name"):
        payload["name"] = _safe_config_id(profile_id).replace("_", " ").title()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _write_new_preset(preset_id: str, source_preset: str, name: str | None, db_path: Path) -> Path:
    safe_id = _safe_config_id(preset_id)
    path = _preset_file_path(safe_id)
    if path.exists():
        raise ValueError(f"Preset already exists: {path}")
    source = get_scoring_preset(source_preset or "balanced", db_path=db_path)
    payload = {
        "id": safe_id,
        "name": name.strip() if name and name.strip() else safe_id.replace("_", " ").title(),
        "description": source.description,
        "order": source.order + 1,
        "is_builtin": False,
        "enabled": True,
        "weights": source.weights.model_dump(mode="json"),
    }
    SCORING_PRESET_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _config_export_paths() -> list[Path]:
    roots = [Path("profiles"), SCORING_PRESET_DIR]
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(path for path in root.rglob("*.json") if path.is_file())
    return sorted(files)


def _config_archive_name(path: Path) -> str:
    profile_root = Path("profiles")
    try:
        return path.relative_to(profile_root.parent).as_posix()
    except ValueError:
        pass
    try:
        return (Path("config") / "scoring_presets" / path.name).as_posix()
    except ValueError:
        return path.name



def _profile_export_paths() -> list[Path]:
    root = Path("profiles")
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.json") if path.is_file())


def _preset_export_paths() -> list[Path]:
    if not SCORING_PRESET_DIR.exists():
        return []
    return sorted(path for path in SCORING_PRESET_DIR.rglob("*.json") if path.is_file())


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
        return "profile", Path("profiles") / target.name
    if len(target.parts) == 3 and target.parts[0] == "config" and target.parts[1] == "scoring_presets":
        return "preset", SCORING_PRESET_DIR / target.name
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
