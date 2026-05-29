from __future__ import annotations

from app.cli.shared import Path, console, typer
from app.desktop.launcher import launch_desktop
from app.desktop.paths import get_desktop_db_path
from app.ui.server import DEFAULT_UI_HOST, DEFAULT_UI_PORT


def desktop(
    db: Path = typer.Option(get_desktop_db_path(), "--db", help="SQLite database path."),
    host: str = typer.Option(DEFAULT_UI_HOST, help="Local server host."),
    port: int = typer.Option(DEFAULT_UI_PORT, min=1, max=65535, help="Local server port."),
    open_browser: bool = typer.Option(True, "--open-browser/--no-open-browser", help="Open the dashboard in the default browser."),
) -> None:
    """Start Job Intel in desktop-style mode."""

    url = f"http://{host}:{port}"
    console.print(f"[bold]Starting Job Intel desktop mode:[/bold] {url}")
    console.print(f"[bold]Database:[/bold] {db}")
    console.print("Keep this process running while using the app.")
    try:
        launch_desktop(db_path=db, host=host, port=port, open_browser=open_browser)
    except RuntimeError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1) from error
