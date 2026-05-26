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

def clear(
    scope: str = typer.Option(..., "--scope", help="Data to clear: rankings, offers, explored, or all."),
    db: Path = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Clear selected app data from SQLite."""

    try:
        if scope not in VALID_CLEAR_SCOPES:
            valid = ", ".join(sorted(VALID_CLEAR_SCOPES))
            raise ValueError(f"Unsupported clear scope: {scope}. Expected one of: {valid}.")
        _print_clear_plan(scope, db)
        if not yes and not typer.confirm("Proceed with clearing this data?"):
            console.print("[yellow]Clear cancelled.[/yellow]")
            raise typer.Exit()
        result = clear_data(db_path=db, scope=scope)
    except ValueError as error:
        console.print(f"[red]Invalid input:[/red] {error}")
        raise typer.Exit(code=1) from error

    console.print("[green]Clear complete.[/green]")
    console.print(
        "Cleared "
        f"explored offers {result.explored}, "
        f"offers {result.offers}, "
        f"rankings {result.rankings}, "
        f"ranking runs {result.ranking_runs}."
    )
