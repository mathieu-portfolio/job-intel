from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

import requests
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.llm.factory import ProviderName
from app.models.evaluation import AiJobEvaluation, FinalDecision, RuleEvaluation, WeightedTermMatch
from app.models.job import JobOffer
from app.storage.sqlite import (
    DEFAULT_DB_PATH,
    DEFAULT_EXPLORED_CAPACITY,
    DEFAULT_RANKED_CAPACITY,
    DEFAULT_UNRANKED_CAPACITY,
    VALID_CLEAR_SCOPES,
    clear_data,
    get_clear_plan,
)
from app.workflows import (
    FetchSource,
    RankingMode,
    fetch_offers,
    rank_offers,
)


app = typer.Typer(help="Fetch and filter technical job offers.", no_args_is_help=True)
console = Console()


def _format_term_matches(matches: list[WeightedTermMatch]) -> str:
    if not matches:
        return "none"
    formatted: list[str] = []
    for match in matches:
        alias = match.matched_alias or match.term
        language = f"/{match.language}" if match.language else ""
        alias_note = f" via {alias}{language}" if alias != match.term or match.language else ""
        formatted.append(f"{match.term}{alias_note} ({match.weight:+g})")
    return ", ".join(formatted)


def _print_clear_plan(scope: str, db: Path) -> None:
    plan = get_clear_plan(db_path=db, scope=scope)
    console.print(f"[bold]Database:[/bold] {db}")
    console.print(f"[bold]Clear scope:[/bold] {plan.scope}")
    console.print("[bold]Will clear:[/bold]")
    console.print(f"- explored offers: {plan.explored}")
    console.print(f"- offers: {plan.offers}")
    console.print(f"- rankings: {plan.rankings}")
    console.print(f"- ranking runs: {plan.ranking_runs}")


@app.callback()
def main() -> None:
    """Job Intel command-line tools."""


@app.command()
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


def _print_ranked_job(
    index: int,
    job: JobOffer,
    rule_evaluation: RuleEvaluation,
    ai_evaluation: AiJobEvaluation | None,
    final_decision: FinalDecision,
) -> None:
    score_table = Table.grid(expand=True)
    score_table.add_column(ratio=1)
    score_table.add_column(ratio=1)
    score_table.add_column(ratio=1)
    score_table.add_row(
        f"[bold]Final[/bold] {final_decision.final_score}",
        f"[bold]Recommendation[/bold] {final_decision.recommendation}",
        f"[bold]Rule[/bold] {final_decision.rule_component}",
    )
    if ai_evaluation is not None:
        score_table.add_row(
            f"[bold]AI[/bold] {final_decision.ai_component}",
            f"[bold]Penalties[/bold] {final_decision.penalty_component}",
            f"[bold]Seniority[/bold] {final_decision.seniority_component}",
        )

    body_lines = [
        "[bold]Job metadata[/bold]",
        f"[bold]{job.company}[/bold] | {job.location or 'Unknown location'}",
        f"Source: {job.source} | Remote: {job.remote if job.remote is not None else 'unknown'}",
        f"{str(job.url)}",
        "",
        "[bold]Rule scoring[/bold]",
        f"Weighted score: {rule_evaluation.score} ({rule_evaluation.normalized_score}/100)",
        f"Rule recommendation: {rule_evaluation.decision}",
        f"Positive terms: {_format_term_matches(rule_evaluation.matched_positive_terms)}",
        f"Negative terms: {_format_term_matches(rule_evaluation.matched_negative_terms)}",
        (
            "Seniority: "
            f"{rule_evaluation.seniority.offer_seniority} offer vs "
            f"{rule_evaluation.seniority.target_seniority} target "
            f"({rule_evaluation.seniority.score}/100)"
        ),
        "",
    ]
    body_lines.extend(f"- {reason}" for reason in rule_evaluation.reasoning)

    if ai_evaluation is not None:
        body_lines.append("")
        body_lines.append("[bold]AI evaluation[/bold]")
        body_lines.append(f"Summary: {ai_evaluation.summary}")
        body_lines.append(
            "Scores: "
            f"fit {ai_evaluation.fit_score}, "
            f"technical {ai_evaluation.technical_fit_score}, "
            f"domain {ai_evaluation.domain_fit_score}, "
            f"role interest {ai_evaluation.role_interest_score}, "
            f"learning {ai_evaluation.learning_potential_score}, "
            f"portfolio {ai_evaluation.portfolio_alignment_score}, "
            f"posting quality {ai_evaluation.posting_quality_score}"
        )
        body_lines.append(f"AI recommendation: {ai_evaluation.recommendation}")
        body_lines.extend(f"[green]-[/green] {reason}" for reason in ai_evaluation.reasoning)
        if ai_evaluation.risks:
            body_lines.append("")
            body_lines.append("[bold red]Risks[/bold red]")
            body_lines.extend(f"[red]-[/red] {risk}" for risk in ai_evaluation.risks)
        if ai_evaluation.suggested_positioning:
            body_lines.append("")
            body_lines.append("[bold]Suggested positioning[/bold]")
            body_lines.extend(f"- {item}" for item in ai_evaluation.suggested_positioning)

    body_lines.append("")
    body_lines.append("[bold]Final decision[/bold]")
    body_lines.append(f"Recommendation: {final_decision.recommendation}")
    body_lines.append(f"Final weighted score: {final_decision.final_score}/100")
    body_lines.extend(f"- {reason}" for reason in final_decision.reasoning)
    if final_decision.policy_adjustments:
        body_lines.append("")
        body_lines.append("[bold yellow]Policy adjustments[/bold yellow]")
        body_lines.extend(f"[yellow]-[/yellow] {item}" for item in final_decision.policy_adjustments)
    if ai_evaluation is None:
        body_lines.append("")
        body_lines.append("[dim]AI evaluation was not used for this result.[/dim]")

    console.print(
        Panel(
            score_table,
            title=f"{index}. {job.title}",
            subtitle=f"Final recommendation: {final_decision.recommendation}",
            expand=True,
        )
    )
    console.print("\n".join(body_lines))
    console.print("-" * 80)


@app.command()
def rank(
    profile: Path = typer.Option(Path("profiles/default.json"), help="Candidate profile JSON path."),
    db: Path = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path."),
    limit: int = typer.Option(10, min=1, help="Maximum number of jobs to evaluate."),
    only_recent_days: int | None = typer.Option(None, min=1, help="Only rank offers seen or published in the last N days."),
    dry_run: bool = typer.Option(False, help="Print jobs that would be evaluated without calling an LLM."),
    min_score: int = typer.Option(40, help="Minimum calibrated rule score before AI evaluation."),
    weights_path: Path | None = typer.Option(None, help="Optional rule scoring weights JSON path."),
    ranking_mode: RankingMode = typer.Option(
        "hybrid",
        "--ranking-mode",
        help="Ranking mode: rules skips LLM, ai evaluates without rule prefilter, hybrid prefilters with rules.",
    ),
    provider: ProviderName | None = typer.Option(
        None,
        help="LLM provider. Defaults to JOB_INTEL_LLM_PROVIDER or openai.",
    ),
    preset: str = typer.Option("balanced", help="Scoring preset for selecting and reviewing screened offers."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print detailed evaluation progress."),
    debug_prompt: bool = typer.Option(False, "--debug-prompt", help="Write evaluation prompts into debug/."),
) -> None:
    """Rank unranked SQLite offers against a candidate profile."""

    try:
        result = rank_offers(
            profile_path=profile,
            db_path=db,
            limit=limit,
            only_recent_days=only_recent_days,
            dry_run=dry_run,
            min_score=min_score,
            weights_path=weights_path,
            ranking_mode=ranking_mode,
            provider=provider,
            preset_id=preset,
            debug_prompt=debug_prompt,
            progress=console.print if verbose else None,
        )
    except FileNotFoundError as error:
        console.print(f"[red]Missing file:[/red] {error.filename}")
        raise typer.Exit(code=1) from error
    except ValueError as error:
        console.print(f"[red]Invalid input:[/red] {error}")
        raise typer.Exit(code=1) from error
    except RuntimeError as error:
        console.print(f"[red]Ranking error:[/red] {error}")
        raise typer.Exit(code=1) from error
    except TimeoutError as error:
        console.print(f"[red]AI ranking timeout:[/red] {error}")
        raise typer.Exit(code=1) from error

    console.print(f"[bold]Profile:[/bold] {profile}")
    console.print(f"[bold]Database:[/bold] {db}")
    console.print(f"[bold]Selected jobs:[/bold] {result.selected_count} screened offers")
    console.print(f"[bold]Ranking mode:[/bold] {ranking_mode}")
    console.print(f"[bold]Prefiltered jobs:[/bold] {result.prefiltered_count}")
    console.print(f"[bold]AI-evaluated jobs:[/bold] {result.ai_evaluation_count}")
    console.print(f"[bold]Skipped jobs:[/bold] {result.skipped_count}")
    if ranking_mode == "hybrid":
        console.print(f"[bold]Hybrid gate:[/bold] calibrated rule score >= {min_score}/100\n")
    else:
        console.print()

    if not result.candidates:
        console.print("[yellow]No screened offers matched this ranking request.[/yellow]")
        raise typer.Exit()

    if dry_run:
        for index, (stored_offer, rule_evaluation) in enumerate(result.candidates, start=1):
            job = stored_offer.job
            console.print(f"[bold]{index}. {job.title}[/bold]")
            console.print(f"{job.company} | {job.location or 'Unknown location'}")
            console.print(
                f"Rule score: {rule_evaluation.score} ({rule_evaluation.normalized_score}/100) | "
                f"Decision: {rule_evaluation.decision}"
            )
            console.print(f"Positive signals: {_format_term_matches(rule_evaluation.matched_positive_terms)}")
            console.print(f"Negative signals: {_format_term_matches(rule_evaluation.matched_negative_terms)}")
            console.print(str(job.url))
            console.print("-" * 80)
        return

    console.print("\n[bold green]Ranked shortlist[/bold green]\n")
    for index, (stored_offer, rule_evaluation, ai_evaluation, final_decision) in enumerate(result.ranked, start=1):
        _print_ranked_job(index, stored_offer.job, rule_evaluation, ai_evaluation, final_decision)
    console.print(f"\n[bold]Saved ranking run:[/bold] {result.run_id} in {db}")


@app.command()
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


@app.command()
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


if __name__ == "__main__":
    app()
