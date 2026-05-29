from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app.desktop.open_path import open_path
from app.desktop.paths import set_desktop_database_path
from app.storage.scoring import get_scoring_preset, list_scoring_presets
from app.ui.config_io import (
    _config_archive_name,
    _config_export_paths,
    _export_json_zip,
    _import_config_zip,
    _import_config_zip_kind,
    _preset_export_paths,
    _preset_payload,
    _profile_export_paths,
    _profile_payload,
    _validate_sqlite_file,
    _write_new_preset,
    _write_new_profile,
    _write_preset,
    _write_profile,
)
from app.resources import app_version, executable_path, is_frozen
from app.ui.context import _active_profile_path, _common_template_context, _safe_local_path
from app.ui.shared import templates
from app.ui.state import _consume_workflow_notice, _form_data, _workflow_notice
from app.ui_options import discover_profiles


def _settings_redirect(tab: str, return_to: str = "/", **params: str) -> RedirectResponse:
    query: dict[str, str] = {"tab": tab}
    for key, value in params.items():
        if value:
            query[key] = value
    if return_to:
        query["return_to"] = return_to
    return RedirectResponse(f"/settings?{urlencode(query)}", status_code=303)


def register_settings_routes(app: FastAPI) -> None:
    @app.post("/settings/profile")
    async def set_active_profile(request: Request):
        form = await _form_data(request)
        selected = (form.get("active_profile") or "").strip()
        valid_values = {profile["value"] for profile in discover_profiles()}
        if selected not in valid_values:
            raise HTTPException(status_code=400, detail="Unknown profile.")
        redirect_to = _safe_local_path(form.get("redirect_to") or request.headers.get("referer"))
        response = RedirectResponse(redirect_to, status_code=303)
        response.set_cookie("job_intel_active_profile", selected, max_age=60 * 60 * 24 * 365, samesite="lax")
        return response

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(
        request: Request,
        tab: str = "profiles",
        profile: str | None = None,
        preset: str = "balanced",
        return_to: str = "/",
    ) -> HTMLResponse:
        settings_tab = tab if tab in {"profiles", "presets", "data", "storage"} else "profiles"
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
                "runtime_paths": request.app.state.runtime_paths,
                "runtime_mode": getattr(request.app.state, "runtime_mode", "cli"),
                "app_version": app_version(),
                "is_frozen": is_frozen(),
                "executable_path": executable_path(),
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
        return _settings_redirect("profiles", return_to=return_to, profile=profile_path)

    @app.post("/settings/profiles/new")
    async def create_profile_from_settings(request: Request):
        form = await _form_data(request)
        return_to = _safe_local_path(form.get("return_to"))
        try:
            path = _write_new_profile(
                form.get("profile_id") or "",
                form.get("source_profile") or _active_profile_path(request),
                form.get("name"),
            )
            profile_path = str(path).replace("\\", "/")
            request.app.state.workflow_notice = _workflow_notice("success", "Profile created", {"Profile": profile_path})
            response = _settings_redirect("profiles", return_to=return_to, profile=profile_path)
            response.set_cookie("job_intel_active_profile", profile_path, max_age=60 * 60 * 24 * 365, samesite="lax")
            return response
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice("error", "Profile creation failed", {"Error": str(error)})
            return _settings_redirect("profiles", return_to=return_to)

    @app.get("/settings/profiles/export")
    def export_profiles(request: Request):
        return _export_json_zip(_profile_export_paths(), "job_intel_profiles.zip")

    @app.post("/settings/profiles/import")
    async def import_profiles(request: Request):
        temp_path: Path | None = None
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
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        return _settings_redirect("profiles")

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
        return _settings_redirect("presets", return_to=return_to, preset=preset_id)

    @app.post("/settings/presets/new")
    async def create_preset_from_settings(request: Request):
        form = await _form_data(request)
        return_to = _safe_local_path(form.get("return_to"))
        try:
            path = _write_new_preset(form.get("preset_id") or "", form.get("source_preset") or "balanced", form.get("name"), request.app.state.db_path)
            preset_id = path.stem
            request.app.state.workflow_notice = _workflow_notice("success", "Preset created", {"Preset": preset_id})
            return _settings_redirect("presets", return_to=return_to, preset=preset_id)
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice("error", "Preset creation failed", {"Error": str(error)})
            return _settings_redirect("presets", return_to=return_to)

    @app.get("/settings/presets/export")
    def export_presets(request: Request):
        return _export_json_zip(_preset_export_paths(), "job_intel_presets.zip")

    @app.post("/settings/presets/import")
    async def import_presets(request: Request):
        temp_path: Path | None = None
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
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        return _settings_redirect("presets")



    @app.post("/settings/open-folder")
    async def open_runtime_folder(request: Request):
        form = await _form_data(request)
        target = (form.get("target") or "").strip()
        runtime_paths = request.app.state.runtime_paths
        allowed_paths = {
            "database": request.app.state.db_path,
        }
        if runtime_paths is not None:
            allowed_paths.update(
                {
                    "data": runtime_paths.data_dir,
                    "profiles": runtime_paths.profiles_dir,
                    "presets": runtime_paths.scoring_presets_dir,
                }
            )
        path = allowed_paths.get(target)
        if path is None:
            request.app.state.workflow_notice = _workflow_notice("error", "Open folder failed", {"Error": "Unknown folder target."})
        else:
            try:
                open_path(Path(path))
                request.app.state.workflow_notice = _workflow_notice("success", "Folder opened", {"Folder": str(Path(path).parent if Path(path).suffix else path)})
            except Exception as error:
                request.app.state.workflow_notice = _workflow_notice("error", "Open folder failed", {"Error": str(error)})
        return _settings_redirect("storage")

    @app.get("/settings/database/path")
    @app.get("/settings/database-location")
    @app.get("/settings/data/database-path")
    def database_path_form_requires_post(request: Request):
        request.app.state.workflow_notice = _workflow_notice(
            "error",
            "Database path change failed",
            {"Error": "Use the Settings form to change the database path."},
        )
        return _settings_redirect("storage")

    @app.post("/settings/database/path")
    @app.post("/settings/database-location")
    @app.post("/settings/data/database-path")
    async def change_database_path(request: Request):
        form = await _form_data(request)
        requested_path = (form.get("database_path") or "").strip()
        move_existing = form.get("move_existing") == "true"
        if not requested_path:
            request.app.state.workflow_notice = _workflow_notice("error", "Database path change failed", {"Error": "Enter a folder or database file path."})
            return _settings_redirect("storage")
        runtime_paths = request.app.state.runtime_paths
        if runtime_paths is None:
            request.app.state.workflow_notice = _workflow_notice("error", "Database path change failed", {"Error": "Database path changes are only available in desktop mode."})
            return _settings_redirect("storage")
        try:
            updated_paths = set_desktop_database_path(runtime_paths, requested_path, move_existing=move_existing)
            request.app.state.runtime_paths = updated_paths
            request.app.state.db_path = updated_paths.db_path
            request.app.state.workflow_notice = _workflow_notice(
                "success",
                "Database path updated",
                {"Database": str(updated_paths.db_path)},
            )
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice("error", "Database path change failed", {"Error": str(error)})
        return _settings_redirect("storage")

    @app.get("/settings/database/export")
    def export_database(request: Request):
        db_file = request.app.state.db_path
        if not db_file.exists():
            raise HTTPException(status_code=404, detail="Database file does not exist yet.")
        return FileResponse(db_file, media_type="application/vnd.sqlite3", filename=f"{db_file.stem}.sqlite")

    @app.post("/settings/database/import")
    async def import_database(request: Request):
        temp_path: Path | None = None
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
            backup_path = db_file.with_suffix(db_file.suffix + ".bak")
            if db_file.exists():
                shutil.copy2(db_file, backup_path)
            shutil.move(str(temp_path), db_file)
            temp_path = None
            request.app.state.workflow_notice = _workflow_notice("success", "Database loaded", {"File": str(db_file), "Backup": str(backup_path) if backup_path.exists() else "none"})
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice("error", "Database load failed", {"Error": str(error)})
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        return _settings_redirect("data")

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
        return FileResponse(archive_path, media_type="application/zip", filename="job_intel_config.zip", background=None)

    @app.post("/settings/config/import")
    async def import_config(request: Request):
        temp_path: Path | None = None
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
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        return _settings_redirect("data")

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
            request.app.state.workflow_notice = _workflow_notice("success", "Preset saved", {"Preset": preset_id, "File": str(path).replace("\\", "/")})
        except Exception as error:
            preset_id = original_id or "balanced"
            request.app.state.workflow_notice = _workflow_notice("error", "Preset save failed", {"Error": str(error)})
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
            request.app.state.workflow_notice = _workflow_notice("success", "Profile saved", {"Profile": str(active_profile).replace("\\", "/")})
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice("error", "Profile save failed", {"Error": str(error)})
        return RedirectResponse("/profile", status_code=303)
