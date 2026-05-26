from __future__ import annotations

from app.cli.shared import (
    DEFAULT_DB_PATH,
    DEFAULT_EXPLORED_CAPACITY,
    DEFAULT_RANKED_CAPACITY,
    DEFAULT_UNRANKED_CAPACITY,
    VALID_CLEAR_SCOPES,
    AiJobEvaluation,
    FetchSource,
    FinalDecision,
    JobOffer,
    Path,
    Panel,
    ProviderName,
    RankingMode,
    RuleEvaluation,
    Table,
    app,
    clear_data,
    console,
    fetch_offers,
    rank_offers,
    requests,
    threading,
    typer,
    webbrowser,
    _format_term_matches,
    _print_clear_plan,
)

def ui(
    db: Path = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path."),
    host: str = typer.Option("127.0.0.1", help="Local server host."),
    port: int = typer.Option(8000, min=1, max=65535, help="Local server port."),
    open_browser: bool = typer.Option(False, "--open-browser", help="Open the dashboard in the default browser."),
) -> None:
    """Start the local review dashboard."""

    try:
        import uvicorn

        from app.ui import create_app
    except ImportError as error:
        console.print(
            "[red]UI dependencies are missing.[/red] Install with `python -m pip install -e .` "
            "or `python -m pip install -r requirements.txt`."
        )
        raise typer.Exit(code=1) from error

    url = f"http://{host}:{port}"
    console.print(f"[bold]Starting review UI:[/bold] {url}")
    console.print(f"[bold]Database:[/bold] {db}")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(db), host=host, port=port)
