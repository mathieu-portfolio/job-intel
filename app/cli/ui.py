from __future__ import annotations

import threading
import webbrowser

from app.cli.shared import DEFAULT_DB_PATH, Path, console, typer
from app.ui.server import DEFAULT_UI_HOST, DEFAULT_UI_PORT, local_url, run_server


def ui(
    db: Path = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path."),
    host: str = typer.Option(DEFAULT_UI_HOST, help="Local server host."),
    port: int = typer.Option(DEFAULT_UI_PORT, min=1, max=65535, help="Local server port."),
    open_browser: bool = typer.Option(False, "--open-browser", help="Open the dashboard in the default browser."),
) -> None:
    """Start the local review dashboard."""

    url = local_url(host, port)
    console.print(f"[bold]Starting review UI:[/bold] {url}")
    console.print(f"[bold]Database:[/bold] {db}")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        run_server(db, host=host, port=port)
    except RuntimeError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1) from error
