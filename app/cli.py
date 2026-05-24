from __future__ import annotations

from typing import Literal

import requests
import typer
from rich.console import Console

from app.filtering.rules import filter_jobs
from app.sources.adzuna import fetch_adzuna
from app.sources.arbeitnow import fetch_arbeitnow
from app.storage.files import save_jobs


app = typer.Typer(help="Fetch and filter technical job offers.", no_args_is_help=True)
console = Console()


@app.callback()
def main() -> None:
    """Job Intel command-line tools."""


@app.command()
def fetch(
    source: Literal["arbeitnow", "adzuna"] = "arbeitnow",
    page: int = typer.Option(1, help="Result page to fetch."),
    query: str = typer.Option("c++ simulation", help="Search query for sources that support it."),
    country: str = typer.Option("fr", help="Adzuna country code, for example fr, gb, us."),
    where: str | None = typer.Option(None, help="Optional Adzuna location filter."),
    min_score: int = typer.Option(10, help="Minimum rule score to print."),
    limit: int = typer.Option(20, help="Maximum number of matches to print."),
) -> None:
    """Fetch jobs from one source, save normalized JSON, and print a shortlist."""

    try:
        if source == "arbeitnow":
            jobs = fetch_arbeitnow(page=page)
        elif source == "adzuna":
            jobs = fetch_adzuna(query=query, country=country, where=where, page=page)
        else:
            raise typer.BadParameter(f"Unsupported source: {source}")
    except requests.RequestException as error:
        console.print(f"[red]Network/API error:[/red] {error}")
        raise typer.Exit(code=1) from error
    except RuntimeError as error:
        console.print(f"[red]Configuration error:[/red] {error}")
        raise typer.Exit(code=1) from error

    save_jobs(jobs)
    matches = filter_jobs(jobs, min_score=min_score)

    console.print(f"[bold]Fetched:[/bold] {len(jobs)} jobs from {source}")
    console.print("[bold]Saved:[/bold] data/normalized/latest_jobs.json")
    console.print(f"[green]Matched:[/green] {len(matches)} jobs with score >= {min_score}\n")

    for index, (job, evaluation) in enumerate(matches[:limit], start=1):
        positives = ", ".join(evaluation.matched_positive_terms) or "none"
        negatives = ", ".join(evaluation.matched_negative_terms) or "none"

        console.print(f"[bold]{index}. {job.title}[/bold]")
        console.print(f"{job.company} | {job.location or 'Unknown location'}")
        console.print(f"Score: {evaluation.score} | Decision: {evaluation.decision}")
        console.print(f"Positive signals: {positives}")
        console.print(f"Negative signals: {negatives}")
        console.print(str(job.url))
        console.print("-" * 80)


if __name__ == "__main__":
    app()
