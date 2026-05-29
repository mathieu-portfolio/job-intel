from __future__ import annotations

from pathlib import Path
from threading import Lock

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.resources import app_version, executable_path, is_frozen
from app.runtime_paths import RuntimePaths
from app.storage.connection import DEFAULT_DB_PATH
from app.ui.routes import register_page_routes, register_settings_routes, register_storage_routes, register_workflow_routes
from app.ui.shared import UI_DIR


def create_app(db_path: Path = DEFAULT_DB_PATH, runtime_paths: RuntimePaths | None = None) -> FastAPI:
    app = FastAPI(title="Job Intel Review")
    app.state.db_path = db_path
    app.state.runtime_paths = runtime_paths
    app.state.runtime_mode = "desktop" if runtime_paths is not None else "cli"
    app.state.workflow_notice = None
    app.state.workflow_cancellations = {}
    app.state.workflow_progress = {}
    app.state.workflow_progress_lock = Lock()

    app.mount("/static", StaticFiles(directory=str(UI_DIR / "static")), name="static")

    register_settings_routes(app)
    register_page_routes(app)
    register_workflow_routes(app)
    register_storage_routes(app)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/runtime")
    def runtime() -> dict[str, str | bool | None]:
        current_runtime_paths = app.state.runtime_paths
        return {
            "app": "Job Intel",
            "version": app_version(),
            "mode": app.state.runtime_mode,
            "frozen": is_frozen(),
            "executable": str(executable_path()),
            "database_path": str(app.state.db_path),
            "data_dir": str(current_runtime_paths.data_dir) if current_runtime_paths else None,
            "profiles_dir": str(current_runtime_paths.profiles_dir) if current_runtime_paths else None,
            "scoring_presets_dir": str(current_runtime_paths.scoring_presets_dir) if current_runtime_paths else None,
        }

    return app
