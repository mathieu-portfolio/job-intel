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

def fetch(
    source: FetchSource = "arbeitnow",
    page: int = typer.Option(1, help="Starting result page to fetch."),
    pages: int = typer.Option(1, "--pages", help="Debug page scan count when --new-offers is not set."),
    new_offers: int | None = typer.Option(None, "--new-offers", help="Target number of newly explored provider offers to process."),
    max_pages: int = typer.Option(10, "--max-pages", help="Maximum provider pages to scan."),
    max_seen_pages: int = typer.Option(
        5,
        "--max-seen-pages",
        help="Stop after this many consecutive pages contain only already-seen offers.",
    ),
    query: str = typer.Option("", help="Manual search query for sources that support it. Overrides profile search queries."),
    country: str = typer.Option("fr", help="Adzuna country code, for example fr, gb, us."),
    where: str | None = typer.Option(None, help="Optional Adzuna location filter."),
    profile: Path = typer.Option(Path("profiles/default.json"), help="Candidate profile JSON path for fast screening."),
    db: Path = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path."),
    min_score: int = typer.Option(40, help="Minimum calibrated rule score to print."),
    limit: int = typer.Option(20, help="Maximum number of matches to print."),
    explored_capacity: int = typer.Option(DEFAULT_EXPLORED_CAPACITY, help="Maximum explored-offer rows to keep."),
    unranked_capacity: int = typer.Option(DEFAULT_UNRANKED_CAPACITY, help="Maximum unranked offer rows to keep."),
    ranked_capacity: int = typer.Option(DEFAULT_RANKED_CAPACITY, help="Maximum ranked offer rows to keep."),
    exploration_mode: str = typer.Option("safe", help="Exploration mode: safe or fast_backfill."),
    use_profile_queries: bool = typer.Option(
        True,
        "--profile-queries/--broad-exploration",
        help="Use profile-owned provider search queries when supported, or disable them for broad exploration.",
    ),
) -> None:
    """Fetch jobs from one source, track newly explored offers, and print a shortlist."""

    try:
        result = fetch_offers(
            source=source,
            page=page,
            pages=pages,
            new_offers=new_offers,
            max_pages=max_pages,
            max_seen_pages=max_seen_pages,
            query=query,
            country=country,
            where=where,
            profile_path=profile,
            db_path=db,
            min_score=min_score,
            explored_capacity=explored_capacity,
            unranked_capacity=unranked_capacity,
            ranked_capacity=ranked_capacity,
            exploration_mode=exploration_mode,  # type: ignore[arg-type]
            use_profile_queries=use_profile_queries,
        )
    except requests.RequestException as error:
        console.print(f"[red]Network/API error:[/red] {error}")
        raise typer.Exit(code=1) from error
    except ValueError as error:
        console.print(f"[red]Invalid input:[/red] {error}")
        raise typer.Exit(code=1) from error
    except RuntimeError as error:
        console.print(f"[red]Configuration error:[/red] {error}")
        raise typer.Exit(code=1) from error

    console.print(f"[bold]Provider rows fetched:[/bold] {result.stats.fetched} jobs from {source}")
    console.print(f"[bold]Pages scanned:[/bold] {result.stats.pages_scanned}")
    console.print(f"[bold]Newly explored:[/bold] {result.stats.newly_explored}")
    console.print(f"[bold]Database:[/bold] {db}")
    console.print(
        f"[green]Inserted:[/green] {result.stats.inserted} | "
        f"[cyan]Updated:[/cyan] {result.stats.updated} | "
        f"[yellow]Already seen:[/yellow] {result.stats.already_seen} | "
        f"[magenta]Filtered out:[/magenta] {result.stats.filtered_out} | "
        f"[red]Errors:[/red] {result.stats.errors}"
    )
    console.print(f"[green]Matched:[/green] {result.matched_count} jobs with calibrated score >= {min_score}\n")
    console.print(
        f"[bold]Pruned:[/bold] explored {result.prune_stats.deleted_explored} | "
        f"unranked {result.prune_stats.deleted_unranked} | "
        f"ranked {result.prune_stats.deleted_ranked}\n"
    )

    for index, (job, evaluation) in enumerate(result.matches[:limit], start=1):
        console.print(f"[bold]{index}. {job.title}[/bold]")
        console.print(f"{job.company} | {job.location or 'Unknown location'}")
        console.print(
            f"Score: {evaluation.score} ({evaluation.normalized_score}/100) | "
            f"Decision: {evaluation.decision}"
        )
        console.print(f"Positive signals: {_format_term_matches(evaluation.matched_positive_terms)}")
        console.print(f"Negative signals: {_format_term_matches(evaluation.matched_negative_terms)}")
        console.print(str(job.url))
        console.print("-" * 80)
