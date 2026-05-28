from __future__ import annotations

from pathlib import Path
from threading import Lock

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.storage.connection import DEFAULT_DB_PATH
from app.ui.routes import register_page_routes, register_settings_routes, register_storage_routes, register_workflow_routes
from app.ui.shared import UI_DIR


def create_app(db_path: Path = DEFAULT_DB_PATH) -> FastAPI:
    app = FastAPI(title="Job Intel Review")
    app.state.db_path = db_path
    app.state.workflow_notice = None
    app.state.workflow_cancellations = {}
    app.state.workflow_progress = {}
    app.state.workflow_progress_lock = Lock()

    app.mount("/static", StaticFiles(directory=str(UI_DIR / "static")), name="static")

    register_settings_routes(app)
    register_page_routes(app)
    register_workflow_routes(app)
    register_storage_routes(app)

    return app
