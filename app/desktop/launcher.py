from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from app.desktop.paths import ensure_desktop_runtime_paths, get_desktop_db_path
from app.ui.server import DEFAULT_UI_HOST, DEFAULT_UI_PORT, local_url, run_server


def wait_until_ready(url: str, *, timeout_seconds: float = 15.0, interval_seconds: float = 0.25) -> None:
    """Wait until the local server answers its health endpoint."""

    health_url = f"{url}/health"
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=1.0) as response:
                if 200 <= response.status < 300:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
        time.sleep(interval_seconds)

    message = f"The local Job Intel server did not become ready at {health_url}."
    if last_error is not None:
        message = f"{message} Last error: {last_error}"
    raise RuntimeError(message)


def open_browser_when_ready(url: str) -> None:
    """Open the browser after the server is ready, without owning server lifetime."""

    wait_until_ready(url)
    webbrowser.open(url)


def launch_desktop(
    *,
    db_path: Path | None = None,
    host: str = DEFAULT_UI_HOST,
    port: int = DEFAULT_UI_PORT,
    open_browser: bool = True,
) -> None:
    """Start Job Intel like a desktop app: local server plus browser UI.

    Uvicorn intentionally runs on the main thread so Ctrl+C is handled normally.
    Browser opening is the only background work.
    """

    runtime_paths = ensure_desktop_runtime_paths()
    resolved_db_path = db_path or runtime_paths.db_path
    resolved_db_path.parent.mkdir(parents=True, exist_ok=True)
    url = local_url(host, port)

    if open_browser:
        browser_thread = threading.Thread(
            target=open_browser_when_ready,
            args=(url,),
            daemon=True,
        )
        browser_thread.start()

    run_server(db_path=resolved_db_path, host=host, port=port, runtime_paths=runtime_paths)
