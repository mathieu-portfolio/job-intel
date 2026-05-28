from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app.storage.connection import DEFAULT_DB_PATH
from app.storage.files import load_profile, profile_id_from_path
from app.storage.maintenance import (
    DEFAULT_EXPLORED_CAPACITY,
    DEFAULT_RANKED_CAPACITY,
    DEFAULT_UNRANKED_CAPACITY,
    clear_data,
    get_storage_counts,
    prune_storage,
)
from app.storage.offers import list_offer_locations, update_offer_status
from app.storage.reviews import (
    get_review_filter_options,
    list_ranked_offers,
    list_unranked_review_offers,
)
from app.storage.scoring import get_scoring_preset, list_scoring_presets, list_screened_offers
from app.filtering.presets import SCORING_PRESET_DIR, ScoringPresetFile
from app.models.profile import CandidateProfile
from app.ui_options import ADZUNA_MARKETS, discover_profiles
from app.workflows import WorkflowCancelled, fetch_offers, rank_offers
from app.ui.state import (
    _cancellation_event,
    _clear_cancellation_event,
    _clear_workflow_progress,
    _consume_workflow_notice,
    _form_data,
    _nonnegative_float,
    _optional_positive_int,
    _positive_int,
    _record_workflow_progress,
    _workflow_notice,
    _workflow_token,
)


UI_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(UI_DIR / "templates"))
DEFAULT_RECENCY_DAYS = 30
CLEAR_SUMMARIES = {
    "rankings": "Deletes AI review rows and legacy ranking rows for the active profile.",
    "offers": "Deletes screened-offer state for the active profile. Shared raw offers remain available to other profiles.",
    "explored": "Deletes provider exploration history for the active profile. Existing screened offers and AI reviews remain.",
    "all": "Deletes explored tracking, screened-offer state, AI reviews, and run metadata for the active profile.",
}



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

def _normalize_review_result(result: dict[str, object]) -> None:
    """Fill display-only score fields for older saved review JSON.

    Existing databases may contain final decisions created before base_component
    existed. This keeps the UI readable until those offers are re-reviewed.
    """
    final = result.get("final_decision")
    if not isinstance(final, dict):
        return
    if final.get("base_component") is not None:
        return
    rule_component = final.get("rule_component")
    ai_component = final.get("ai_component")
    try:
        if ai_component is None:
            final["base_component"] = int(rule_component)
        else:
            final["base_component"] = round((int(rule_component) + int(ai_component)) / 2)
    except (TypeError, ValueError):
        pass

def _normalize_review_offers(offers: list[dict[str, object]]) -> None:
    for offer in offers:
        result = offer.get("result")
        if isinstance(result, dict):
            _normalize_review_result(result)

def create_app(db_path: Path = DEFAULT_DB_PATH) -> FastAPI:
    app = FastAPI(title="Job Intel Review")
    app.state.db_path = db_path
    app.state.workflow_notice = None
    app.state.workflow_cancellations = {}
    app.state.workflow_progress = {}
    app.state.workflow_progress_lock = Lock()
    app.mount("/static", StaticFiles(directory=str(UI_DIR / "static")), name="static")


    @app.post("/settings/profile")
    async def set_active_profile(request: Request):
        form = await _form_data(request)
        selected = (form.get("active_profile") or "").strip()
        valid_values = {profile["value"] for profile in discover_profiles()}
        if selected not in valid_values:
            raise HTTPException(status_code=400, detail="Unknown profile.")
        redirect_to = (form.get("redirect_to") or request.headers.get("referer") or "/").strip()
        if not redirect_to.startswith("/") or redirect_to.startswith("//"):
            redirect_to = "/"
        response = RedirectResponse(redirect_to, status_code=303)
        response.set_cookie("job_intel_active_profile", selected, max_age=60 * 60 * 24 * 365, samesite="lax")
        return response

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, tab: str = "profiles", profile: str | None = None, preset: str = "balanced", return_to: str = "/") -> HTMLResponse:
        settings_tab = tab if tab in {"profiles", "presets", "data"} else "profiles"
        return_to = _safe_local_path(return_to)
        selected_profile_path = profile if profile in {item["value"] for item in discover_profiles()} else _active_profile_path(request)
        scoring_presets = list_scoring_presets(request.app.state.db_path, enabled_only=False)
        selected_preset = get_scoring_preset(preset, db_path=request.app.state.db_path)
        profile_payload = _profile_payload(selected_profile_path)
        preset_payload = _preset_payload(selected_preset.id)
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                **_common_template_context(request),
                **profile_payload,
                "selected_profile_path": selected_profile_path,
                "settings_tab": settings_tab,
                "scoring_presets": scoring_presets,
                "selected_preset": selected_preset,
                "preset": preset_payload["preset"],
                "preset_payload_json": preset_payload["payload_json"],
                "profile_export_count": len(_profile_export_paths()),
                "preset_export_count": len(_preset_export_paths()),
                "db_exists": request.app.state.db_path.exists(),
                "db_path": request.app.state.db_path,
                "workflow_notice": _consume_workflow_notice(request),
                "active_page": "settings",
                "return_to": return_to,
            },
        )

    @app.post("/settings/profiles")
    async def save_profile_from_settings(request: Request):
        form = await _form_data(request)
        return_to = _safe_local_path(form.get("return_to"))
        profile_path = (form.get("profile_path") or _active_profile_path(request)).strip()
        try:
            payload = json.loads(form.get("profile_payload") or "{}")
            if not isinstance(payload, dict):
                raise ValueError("Profile payload must be an object.")
            _write_profile(Path(profile_path), payload)
            request.app.state.workflow_notice = _workflow_notice("success", "Profile saved", {"Profile": profile_path})
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice("error", "Profile save failed", {"Error": str(error)})
        return RedirectResponse(f"/settings?tab=profiles&profile={profile_path}&return_to={return_to}", status_code=303)

    @app.post("/settings/profiles/new")
    async def create_profile_from_settings(request: Request):
        form = await _form_data(request)
        return_to = _safe_local_path(form.get("return_to"))
        try:
            path = _write_new_profile(form.get("profile_id") or "", form.get("source_profile") or _active_profile_path(request), form.get("name"))
            profile_path = str(path).replace("\\", "/")
            request.app.state.workflow_notice = _workflow_notice("success", "Profile created", {"Profile": profile_path})
            response = RedirectResponse(f"/settings?tab=profiles&profile={profile_path}&return_to={return_to}", status_code=303)
            response.set_cookie("job_intel_active_profile", profile_path, max_age=60 * 60 * 24 * 365, samesite="lax")
            return response
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice("error", "Profile creation failed", {"Error": str(error)})
            return RedirectResponse(f"/settings?tab=profiles&return_to={return_to}", status_code=303)

    @app.post("/settings/presets")
    async def save_preset_from_settings(request: Request):
        form = await _form_data(request)
        return_to = _safe_local_path(form.get("return_to"))
        original_id = (form.get("original_preset_id") or "balanced").strip()
        try:
            payload = json.loads(form.get("preset_payload") or "{}")
            if not isinstance(payload, dict):
                raise ValueError("Preset payload must be an object.")
            path = _write_preset(original_id, payload)
            preset_id = str(payload.get("id") or original_id)
            request.app.state.workflow_notice = _workflow_notice("success", "Preset saved", {"Preset": preset_id, "File": str(path).replace("\\", "/")})
        except Exception as error:
            preset_id = original_id
            request.app.state.workflow_notice = _workflow_notice("error", "Preset save failed", {"Error": str(error)})
        return RedirectResponse(f"/settings?tab=presets&preset={preset_id}&return_to={return_to}", status_code=303)

    @app.post("/settings/presets/new")
    async def create_preset_from_settings(request: Request):
        form = await _form_data(request)
        return_to = _safe_local_path(form.get("return_to"))
        try:
            path = _write_new_preset(form.get("preset_id") or "", form.get("source_preset") or "balanced", form.get("name"), request.app.state.db_path)
            preset_id = path.stem
            request.app.state.workflow_notice = _workflow_notice("success", "Preset created", {"Preset": preset_id})
            return RedirectResponse(f"/settings?tab=presets&preset={preset_id}&return_to={return_to}", status_code=303)
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice("error", "Preset creation failed", {"Error": str(error)})
            return RedirectResponse(f"/settings?tab=presets&return_to={return_to}", status_code=303)


    @app.get("/settings/profiles/export")
    def export_profiles(request: Request):
        return _export_json_zip(_profile_export_paths(), "job_intel_profiles.zip")

    @app.post("/settings/profiles/import")
    async def import_profiles(request: Request):
        try:
            form = await request.form()
            config_file = form.get("profile_config_file")
            if config_file is None or not hasattr(config_file, "file"):
                raise ValueError("Choose a profiles zip to import.")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
                temp_path = Path(tmp.name)
                shutil.copyfileobj(config_file.file, tmp)
            counts = _import_config_zip_kind(temp_path, "profile")
            request.app.state.workflow_notice = _workflow_notice("success", "Profiles imported", {"Profiles": counts["profiles"], "Skipped files": counts["skipped"]})
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice("error", "Profile import failed", {"Error": str(error)})
        finally:
            try:
                temp_path.unlink()  # type: ignore[name-defined]
            except Exception:
                pass
        return RedirectResponse("/settings?tab=profiles", status_code=303)

    @app.get("/settings/presets/export")
    def export_presets(request: Request):
        return _export_json_zip(_preset_export_paths(), "job_intel_presets.zip")

    @app.post("/settings/presets/import")
    async def import_presets(request: Request):
        try:
            form = await request.form()
            config_file = form.get("preset_config_file")
            if config_file is None or not hasattr(config_file, "file"):
                raise ValueError("Choose a presets zip to import.")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
                temp_path = Path(tmp.name)
                shutil.copyfileobj(config_file.file, tmp)
            counts = _import_config_zip_kind(temp_path, "preset")
            request.app.state.workflow_notice = _workflow_notice("success", "Presets imported", {"Presets": counts["presets"], "Skipped files": counts["skipped"]})
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice("error", "Preset import failed", {"Error": str(error)})
        finally:
            try:
                temp_path.unlink()  # type: ignore[name-defined]
            except Exception:
                pass
        return RedirectResponse("/settings?tab=presets", status_code=303)



    @app.get("/settings/database/export")
    def export_database(request: Request):
        db_file = request.app.state.db_path
        if not db_file.exists():
            raise HTTPException(status_code=404, detail="Database file does not exist yet.")
        return FileResponse(
            db_file,
            media_type="application/vnd.sqlite3",
            filename=f"{db_file.stem}.sqlite",
        )

    @app.post("/settings/database/import")
    async def import_database(request: Request):
        try:
            form = await request.form()
            database_file = form.get("database_file")
            if database_file is None or not hasattr(database_file, "file"):
                raise ValueError("Choose a database file to load.")
            suffix = Path(getattr(database_file, "filename", "database.sqlite") or "database.sqlite").suffix or ".sqlite"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                temp_path = Path(tmp.name)
                shutil.copyfileobj(database_file.file, tmp)
            _validate_sqlite_file(temp_path)
            db_file = request.app.state.db_path
            db_file.parent.mkdir(parents=True, exist_ok=True)
            if db_file.exists():
                backup_path = db_file.with_suffix(db_file.suffix + ".bak")
                shutil.copy2(db_file, backup_path)
            shutil.move(str(temp_path), db_file)
            request.app.state.workflow_notice = _workflow_notice("success", "Database loaded", {"File": str(db_file), "Backup": str(db_file.with_suffix(db_file.suffix + ".bak")) if db_file.with_suffix(db_file.suffix + ".bak").exists() else "none"})
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice("error", "Database load failed", {"Error": str(error)})
        finally:
            try:
                temp_path.unlink()  # type: ignore[name-defined]
            except Exception:
                pass
        return RedirectResponse("/settings?tab=data", status_code=303)

    @app.get("/settings/config/export")
    def export_config(request: Request):
        files = _config_export_paths()
        if not files:
            raise HTTPException(status_code=404, detail="No profile or preset JSON files found.")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            archive_path = Path(tmp.name)
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, _config_archive_name(path))
        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename="job_intel_config.zip",
            background=None,
        )

    @app.post("/settings/config/import")
    async def import_config(request: Request):
        try:
            form = await request.form()
            config_file = form.get("config_file")
            if config_file is None or not hasattr(config_file, "file"):
                raise ValueError("Choose a config zip to import.")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
                temp_path = Path(tmp.name)
                shutil.copyfileobj(config_file.file, tmp)
            counts = _import_config_zip(temp_path)
            request.app.state.workflow_notice = _workflow_notice("success", "Configuration imported", {"Profiles": counts["profiles"], "Presets": counts["presets"], "Skipped files": counts["skipped"]})
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice("error", "Configuration import failed", {"Error": str(error)})
        finally:
            try:
                temp_path.unlink()  # type: ignore[name-defined]
            except Exception:
                pass
        return RedirectResponse("/settings?tab=data", status_code=303)


    @app.get("/presets", response_class=HTMLResponse)
    def presets_editor(request: Request, preset: str = "balanced") -> RedirectResponse:
        return RedirectResponse(f"/settings?tab=presets&preset={preset}", status_code=303)

    @app.post("/presets")
    async def save_preset(request: Request):
        form = await _form_data(request)
        original_id = (form.get("original_preset_id") or "").strip()
        try:
            payload = json.loads(form.get("preset_payload") or "{}")
            if not isinstance(payload, dict):
                raise ValueError("Preset payload must be an object.")
            path = _write_preset(original_id, payload)
            preset_id = str(payload.get("id") or original_id)
            request.app.state.workflow_notice = _workflow_notice(
                "success",
                "Preset saved",
                {"Preset": preset_id, "File": str(path).replace("\\", "/")},
            )
        except Exception as error:
            preset_id = original_id or "balanced"
            request.app.state.workflow_notice = _workflow_notice(
                "error",
                "Preset save failed",
                {"Error": str(error)},
            )
        return RedirectResponse(f"/presets?preset={preset_id}", status_code=303)

    @app.get("/profile", response_class=HTMLResponse)
    def profile_editor(request: Request) -> RedirectResponse:
        return RedirectResponse("/settings?tab=profiles", status_code=303)

    @app.post("/profile")
    async def save_profile(request: Request):
        form = await _form_data(request)
        active_profile = Path(_active_profile_path(request))
        try:
            payload = json.loads(form.get("profile_payload") or "{}")
            if not isinstance(payload, dict):
                raise ValueError("Profile payload must be an object.")
            _write_profile(active_profile, payload)
            request.app.state.workflow_notice = _workflow_notice(
                "success",
                "Profile saved",
                {"Profile": str(active_profile).replace("\\", "/")},
            )
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice(
                "error",
                "Profile save failed",
                {"Error": str(error)},
            )
        return RedirectResponse("/profile", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    @app.get("/ai-reviewed", response_class=HTMLResponse)
    def index(
        request: Request,
        recommendation: str | None = None,
        status: str | None = None,
        source: str | None = None,
        location: str | None = None,
        ranking_mode: str | None = None,
        recency: str | None = None,
        ai_only: bool = False,
        preset: str = "balanced",
        sort: str = "score_desc",
        limit: int = 100,
    ) -> HTMLResponse:
        recency_days = _positive_int(recency, DEFAULT_RECENCY_DAYS)
        active_profile = _active_profile_path(request)
        scoring_presets = list_scoring_presets(request.app.state.db_path, enabled_only=True)
        selected_preset = get_scoring_preset(preset, db_path=request.app.state.db_path)
        offers = list_ranked_offers(
            db_path=request.app.state.db_path,
            recommendation=recommendation or None,
            status=status or None,
            source=source or None,
            location=location or None,
            ranking_mode=ranking_mode or None,
            profile_path=active_profile,
            preset=selected_preset,
            only_recent_days=recency_days,
            ai_only=ai_only,
            sort=sort,
            limit=limit,
        )
        _normalize_review_offers(offers)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "offers": offers,
                "filters": {
                    "recommendation": recommendation or "",
                    "status": status or "",
                    "source": source or "",
                    "location": location or "",
                    "ranking_mode": ranking_mode or "",
                    "recency": recency_days,
                    "ai_only": ai_only,
                    "preset": selected_preset.id,
                    "sort": sort,
                    "limit": limit,
                },
                "options": get_review_filter_options(request.app.state.db_path),
                "scoring_presets": scoring_presets,
                **_common_template_context(request),
                "location_suggestions": list_offer_locations(request.app.state.db_path),
                "db_path": request.app.state.db_path,
                "workflow_notice": _consume_workflow_notice(request),
                "workflow_count_label": f"{len(offers)} AI reviewed offers",
                "active_page": "ai_reviewed",
            },
        )

    @app.get("/explore", response_class=HTMLResponse)
    @app.get("/offers", response_class=HTMLResponse)
    def fetched_offers(
        request: Request,
        q: str | None = None,
        source: str | None = None,
        limit: int = 100,
    ) -> HTMLResponse:
        active_profile = _active_profile_path(request)
        offers = list_unranked_review_offers(
            db_path=request.app.state.db_path,
            search=q or None,
            source=source or None,
            profile_id=profile_id_from_path(active_profile),
            limit=limit,
        )
        return templates.TemplateResponse(
            request,
            "offers.html",
            {
                "offers": offers,
                "filters": {
                    "q": q or "",
                    "source": source or "",
                    "limit": limit,
                },
                "options": get_review_filter_options(request.app.state.db_path),
                "location_suggestions": list_offer_locations(request.app.state.db_path),
                "adzuna_markets": ADZUNA_MARKETS,
                **_common_template_context(request),
                "db_path": request.app.state.db_path,
                "workflow_notice": _consume_workflow_notice(request),
                "storage_capacities": {
                    "explored": DEFAULT_EXPLORED_CAPACITY,
                    "unranked": DEFAULT_UNRANKED_CAPACITY,
                    "ranked": DEFAULT_RANKED_CAPACITY,
                },
                "show_fetch_workflow": True,
                "page_title": "Explore",
                "empty_message": "No screened offers match these filters.",
                "listing_path": "/explore",
                "workflow_count_label": f"{len(offers)} offers",
                "active_page": "explore",
            },
        )

    @app.get("/screened", response_class=HTMLResponse)
    def screened_offers(
        request: Request,
        q: str | None = None,
        source: str | None = None,
        preset: str = "balanced",
        show_all_presets: bool = False,
        sort: str = "score_desc",
        limit: int = 100,
    ) -> HTMLResponse:
        active_profile = _active_profile_path(request)
        offers = list_screened_offers(
            db_path=request.app.state.db_path,
            preset_id=preset,
            profile_id=profile_id_from_path(active_profile),
            show_all_matching_presets=show_all_presets,
            search=q or None,
            source=source or None,
            sort=sort,
            limit=limit,
        )
        scoring_presets = list_scoring_presets(request.app.state.db_path, enabled_only=True)
        return templates.TemplateResponse(
            request,
            "offers.html",
            {
                "offers": offers,
                "filters": {
                    "q": q or "",
                    "source": source or "",
                    "preset": preset,
                    "show_all_presets": show_all_presets,
                    "sort": sort,
                    "limit": limit,
                },
                "options": get_review_filter_options(request.app.state.db_path),
                "scoring_presets": scoring_presets,
                "location_suggestions": list_offer_locations(request.app.state.db_path),
                "adzuna_markets": ADZUNA_MARKETS,
                **_common_template_context(request),
                "db_path": request.app.state.db_path,
                "workflow_notice": _consume_workflow_notice(request),
                "storage_capacities": {
                    "explored": DEFAULT_EXPLORED_CAPACITY,
                    "unranked": DEFAULT_UNRANKED_CAPACITY,
                    "ranked": DEFAULT_RANKED_CAPACITY,
                },
                "show_fetch_workflow": False,
                "show_screened_filters": True,
                "page_title": "Screened",
                "empty_message": "No screened offers match these filters.",
                "listing_path": "/screened",
                "workflow_count_label": f"{len(offers)} screened",
                "active_page": "screened",
            },
        )

    @app.get("/maintenance", response_class=HTMLResponse)
    def maintenance(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "maintenance.html",
            {
                "db_path": request.app.state.db_path,
                "workflow_notice": _consume_workflow_notice(request),
                "storage_counts": get_storage_counts(
                    request.app.state.db_path,
                    profile_id=profile_id_from_path(_active_profile_path(request)),
                ),
                "clear_summaries": CLEAR_SUMMARIES,
                **_common_template_context(request),
                "workflow_count_label": "Maintenance",
                "active_page": "maintenance",
            },
        )

    @app.post("/workflows/fetch")
    async def run_fetch(request: Request):
        form = await _form_data(request)
        token = _workflow_token(request)
        cancellation = _cancellation_event(request, token)
        progress = lambda message: _record_workflow_progress(request, token, message)
        try:
            preview_limit = _positive_int(form.get("limit"), 20)
            target_unexplored_offers = _positive_int(form.get("new_offers"), 20)
            progress(f"Processed 0/{target_unexplored_offers} new/unexplored offers.")
            source = form.get("source") or "arbeitnow"
            country = (form.get("country") or "").strip()
            if source == "adzuna" and not country:
                raise ValueError("Market is required when fetching from Adzuna.")
            result = await run_in_threadpool(
                fetch_offers,
                source=source,  # type: ignore[arg-type]
                page=_positive_int(form.get("page"), 1),
                new_offers=target_unexplored_offers,
                max_pages=_positive_int(form.get("max_pages"), 10),
                max_seen_pages=_positive_int(form.get("max_seen_pages"), 50),
                query=(form.get("query") or "").strip(),
                country=country or "fr",
                where=(form.get("location") or "").strip() or None,
                profile_path=Path(_active_profile_path(request)),
                db_path=request.app.state.db_path,
                min_score=None,
                explored_capacity=_positive_int(form.get("explored_capacity"), DEFAULT_EXPLORED_CAPACITY),
                unranked_capacity=_positive_int(form.get("unranked_capacity"), DEFAULT_UNRANKED_CAPACITY),
                ranked_capacity=_positive_int(form.get("ranked_capacity"), DEFAULT_RANKED_CAPACITY),
                exploration_mode=(form.get("exploration_mode") or "safe"),  # type: ignore[arg-type]
                use_profile_queries=form.get("use_profile_queries") == "true",
                fetch_concurrency=_positive_int(form.get("fetch_concurrency"), 1),
                provider_retry_attempts=_positive_int(form.get("provider_retry_attempts"), 1),
                provider_retry_backoff=_nonnegative_float(form.get("provider_retry_backoff"), 0.0),
                progress=progress,
                cancelled=cancellation.is_set,
            )
            request.app.state.workflow_notice = _workflow_notice(
                "success",
                "Fetch complete",
                {
                    "Pages scanned": result.stats.pages_scanned,
                    "Provider rows fetched": result.stats.fetched,
                    "Newly explored": result.stats.newly_explored,
                    "Already seen": result.stats.already_seen,
                    "Screened": result.stats.inserted + result.stats.updated,
                    "Screened out": result.stats.filtered_out,
                    "Inserted": result.stats.inserted,
                    "Updated": result.stats.updated,
                    "Errors": result.stats.errors,
                    "Pruned explored": result.prune_stats.deleted_explored,
                    "Pruned unranked": result.prune_stats.deleted_unranked,
                    "Pruned ranked": result.prune_stats.deleted_ranked,
                    "Matched": result.matched_count,
                    "Source": result.source,
                    "Preview limit": preview_limit,
                },
                [
                    f"{job.title} at {job.company} ({evaluation.normalized_score}/100)"
                    for job, evaluation in result.matches[:preview_limit]
                ],
            )
        except WorkflowCancelled:
            request.app.state.workflow_notice = _workflow_notice(
                "error",
                "Fetch cancelled",
                {"Status": "Cancelled by user"},
            )
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice(
                "error",
                "Fetch failed",
                {"Error": str(error)},
            )
        finally:
            _clear_cancellation_event(request, token)
            _clear_workflow_progress(request, token)
        return RedirectResponse("/explore", status_code=303)

    @app.post("/workflows/rank")
    async def run_rank(request: Request):
        form = await _form_data(request)
        token = _workflow_token(request)
        cancellation = _cancellation_event(request, token)
        progress = lambda message: _record_workflow_progress(request, token, message)
        try:
            result = await run_in_threadpool(
                rank_offers,
                profile_path=Path(_active_profile_path(request)),
                db_path=request.app.state.db_path,
                limit=_positive_int(form.get("limit"), 10),
                only_recent_days=_optional_positive_int(form.get("only_recent_days")),
                ranking_mode="ai",
                provider=((form.get("provider") or "").strip() or None),  # type: ignore[arg-type]
                model=(form.get("model") or "").strip() or None,
                ai_concurrency=_positive_int(form.get("ai_concurrency"), 1),
                ai_retry_attempts=_positive_int(form.get("ai_retry_attempts"), 1),
                ai_retry_backoff=_nonnegative_float(form.get("ai_retry_backoff"), 0.0),
                ai_abort_on_error=form.get("ai_abort_on_error") == "true",
                progress=progress,
                cancelled=cancellation.is_set,
            )
            request.app.state.workflow_notice = _workflow_notice(
                "success",
                "Rank complete",
                {
                    "Selected jobs": result.selected_count,
                    "Reviewed": result.saved_count,
                    "AI-evaluated jobs": result.ai_evaluation_count,
                    "Skipped jobs": result.skipped_count,
                    "Saved AI reviews": result.saved_count,
                    "Run": result.run_id or "none",
                },
                result.messages,
            )
        except WorkflowCancelled:
            request.app.state.workflow_notice = _workflow_notice(
                "error",
                "Rank cancelled",
                {"Status": "Cancelled by user"},
            )
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice(
                "error",
                "Rank failed",
                {"Error": str(error)},
            )
        finally:
            _clear_cancellation_event(request, token)
            _clear_workflow_progress(request, token)
        return RedirectResponse("/", status_code=303)

    @app.get("/workflows/progress/{token}")
    async def workflow_progress(request: Request, token: str):
        with request.app.state.workflow_progress_lock:
            progress = dict(request.app.state.workflow_progress.get(token, {}))
        return progress

    @app.post("/workflows/cancel")
    async def cancel_workflow(request: Request):
        form = await _form_data(request)
        token = (form.get("token") or request.headers.get("x-workflow-token") or "").strip()
        event = request.app.state.workflow_cancellations.get(token)
        if event is not None:
            event.set()
        return {"cancelled": event is not None}

    @app.post("/offers/{offer_id}/status/{status}", response_model=None)
    def set_offer_status(request: Request, offer_id: int, status: str):
        try:
            update_offer_status(
                db_path=request.app.state.db_path,
                offer_id=offer_id,
                status=status,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if request.headers.get("HX-Request") != "true":
            return RedirectResponse(
                request.headers.get("referer") or "/",
                status_code=303,
            )
        return templates.TemplateResponse(
            request,
            "_status_controls.html",
            {"offer": {"offer_id": offer_id, "review_status": status}},
        )

    @app.post("/rankings/clear")
    def clear_all_rankings(request: Request):
        clear_data(
            db_path=request.app.state.db_path,
            scope="rankings",
            profile_id=profile_id_from_path(_active_profile_path(request)),
        )
        return RedirectResponse("/", status_code=303)

    @app.post("/storage/clear")
    async def clear_storage_action(request: Request):
        form = await _form_data(request)
        scope = (form.get("scope") or "").strip()
        try:
            result = clear_data(
                db_path=request.app.state.db_path,
                scope=scope,
                profile_id=profile_id_from_path(_active_profile_path(request)),
            )
            request.app.state.workflow_notice = _workflow_notice(
                "success",
                "Clear complete",
                {
                    "Scope": result.scope,
                    "Deleted explored": result.explored,
                    "Deleted offers": result.offers,
                    "Deleted AI reviews": result.rankings,
                    "Deleted ranking runs": result.ranking_runs,
                },
            )
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice(
                "error",
                "Clear failed",
                {"Error": str(error)},
            )
        redirect_to = (form.get("redirect_to") or request.headers.get("referer") or "/").strip()
        if not redirect_to.startswith("/") or redirect_to.startswith("//"):
            redirect_to = "/"
        return RedirectResponse(redirect_to, status_code=303)

    @app.post("/storage/prune")
    async def prune_storage_action(request: Request):
        form = await _form_data(request)
        try:
            result = prune_storage(
                request.app.state.db_path,
                explored_capacity=_positive_int(form.get("explored_capacity"), DEFAULT_EXPLORED_CAPACITY),
                unranked_capacity=_positive_int(form.get("unranked_capacity"), DEFAULT_UNRANKED_CAPACITY),
                ranked_capacity=_positive_int(form.get("ranked_capacity"), DEFAULT_RANKED_CAPACITY),
            )
            request.app.state.workflow_notice = _workflow_notice(
                "success",
                "Cleanup complete",
                {
                    "Deleted explored": result.deleted_explored,
                    "Deleted unranked": result.deleted_unranked,
                    "Deleted ranked": result.deleted_ranked,
                    "Explored count": result.after.explored,
                    "Unranked count": result.after.unranked,
                    "Ranked count": result.after.ranked,
                },
            )
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice(
                "error",
                "Cleanup failed",
                {"Error": str(error)},
            )
        return RedirectResponse("/", status_code=303)

    return app
