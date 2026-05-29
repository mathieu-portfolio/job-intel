from __future__ import annotations

from pathlib import Path

from app.runtime_paths import RuntimePaths
from app.storage.connection import DEFAULT_DB_PATH
from app.ui import create_app

DEFAULT_UI_HOST = "127.0.0.1"
DEFAULT_UI_PORT = 8000


def local_url(host: str = DEFAULT_UI_HOST, port: int = DEFAULT_UI_PORT) -> str:
    return f"http://{host}:{port}"


def run_server(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    host: str = DEFAULT_UI_HOST,
    port: int = DEFAULT_UI_PORT,
    runtime_paths: RuntimePaths | None = None,
) -> None:
    """Run the local FastAPI review dashboard."""

    try:
        import uvicorn
    except ImportError as error:
        raise RuntimeError(
            "UI dependencies are missing. Install with `python -m pip install -e .` "
            "or `python -m pip install -r requirements.txt`."
        ) from error

    uvicorn.run(create_app(db_path, runtime_paths=runtime_paths), host=host, port=port)
