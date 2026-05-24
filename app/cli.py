from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Literal

import requests
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.ai.evaluator import build_job_evaluation_prompts, evaluate_job_with_ai
from app.filtering.rules import filter_jobs
from app.llm.factory import ProviderName, create_llm_provider
from app.llm.ollama_client import OllamaLlmProvider
from app.models.evaluation import AiJobEvaluation, RuleEvaluation
from app.models.job import JobOffer
from app.sources.adzuna import fetch_adzuna
from app.sources.arbeitnow import fetch_arbeitnow
from app.storage.files import LATEST_NORMALIZED_PATH, load_jobs, load_profile, save_jobs


app = typer.Typer(help="Fetch and filter technical job offers.", no_args_is_help=True)
console = Console()


def _format_timeout(timeout_seconds: float | None) -> str:
    if timeout_seconds is None:
        return "provider default"
    return f"{timeout_seconds:g}s"


def _prompt_size(system_prompt: str, user_prompt: str) -> int:
    return len(system_prompt.encode("utf-8")) + len(user_prompt.encode("utf-8"))


def _safe_debug_name(index: int, title: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", title.lower()).strip("-")
    return f"{index:03d}-{cleaned[:60] or 'job'}"


def _dump_debug_prompts(
    *,
    index: int,
    job: JobOffer,
    system_prompt: str,
    user_prompt: str,
) -> None:
    debug_dir = Path("debug")
    debug_dir.mkdir(exist_ok=True)
    base_name = _safe_debug_name(index, job.title)
    (debug_dir / f"{base_name}.system.txt").write_text(system_prompt, encoding="utf-8")
    (debug_dir / f"{base_name}.user.json").write_text(user_prompt, encoding="utf-8")


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


def _print_ranked_job(
    index: int,
    job: JobOffer,
    rule_evaluation: RuleEvaluation,
    ai_evaluation: AiJobEvaluation,
) -> None:
    score_table = Table.grid(expand=True)
    score_table.add_column(ratio=1)
    score_table.add_column(ratio=1)
    score_table.add_column(ratio=1)
    score_table.add_row(
        f"[bold]Fit[/bold] {ai_evaluation.fit_score}",
        f"[bold]Technical[/bold] {ai_evaluation.technical_fit_score}",
        f"[bold]Junior[/bold] {ai_evaluation.junior_accessibility_score}",
    )
    score_table.add_row(
        f"[bold]Learning[/bold] {ai_evaluation.learning_potential_score}",
        f"[bold]Portfolio[/bold] {ai_evaluation.portfolio_alignment_score}",
        f"[bold]Wording risk[/bold] {ai_evaluation.wording_risk_score}",
    )

    body_lines = [
        f"[bold]{job.company}[/bold] | {job.location or 'Unknown location'}",
        f"{str(job.url)}",
        "",
        f"[bold]Recommendation:[/bold] {ai_evaluation.recommendation}",
        f"[bold]Rule score:[/bold] {rule_evaluation.score} ({rule_evaluation.decision})",
        "",
    ]
    body_lines.extend(f"[green]-[/green] {reason}" for reason in ai_evaluation.reasoning)
    if ai_evaluation.risks:
        body_lines.append("")
        body_lines.append("[bold red]Risks[/bold red]")
        body_lines.extend(f"[red]-[/red] {risk}" for risk in ai_evaluation.risks)
    if ai_evaluation.suggested_positioning:
        body_lines.append("")
        body_lines.append("[bold]Suggested positioning[/bold]")
        body_lines.extend(f"- {item}" for item in ai_evaluation.suggested_positioning)

    console.print(
        Panel(
            score_table,
            title=f"{index}. {job.title}",
            subtitle=f"AI fit score: {ai_evaluation.fit_score}",
            expand=True,
        )
    )
    console.print("\n".join(body_lines))
    console.print("-" * 80)


@app.command()
def rank(
    profile: Path = typer.Option(Path("profiles/default.json"), help="Candidate profile JSON path."),
    jobs_path: Path = typer.Option(LATEST_NORMALIZED_PATH, help="Normalized jobs JSON path."),
    limit: int = typer.Option(10, min=1, help="Maximum number of jobs to evaluate."),
    dry_run: bool = typer.Option(False, help="Print jobs that would be evaluated without calling an LLM."),
    min_score: int = typer.Option(10, help="Minimum cheap rule score before AI evaluation."),
    provider: ProviderName | None = typer.Option(
        None,
        help="LLM provider. Defaults to JOB_INTEL_LLM_PROVIDER or openai.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print detailed evaluation progress."),
    debug_prompt: bool = typer.Option(False, "--debug-prompt", help="Write evaluation prompts into debug/."),
) -> None:
    """Rank normalized jobs against a candidate profile using an LLM provider."""

    try:
        candidate_profile = load_profile(profile)
        jobs = load_jobs(jobs_path)
    except FileNotFoundError as error:
        console.print(f"[red]Missing file:[/red] {error.filename}")
        raise typer.Exit(code=1) from error
    except ValueError as error:
        console.print(f"[red]Invalid input:[/red] {error}")
        raise typer.Exit(code=1) from error

    candidates = filter_jobs(jobs, min_score=min_score, profile=candidate_profile)[:limit]

    console.print(f"[bold]Profile:[/bold] {profile}")
    console.print(f"[bold]Jobs loaded:[/bold] {len(jobs)}")
    console.print(f"[bold]After rule filter:[/bold] {len(candidates)} jobs with score >= {min_score}\n")

    if not candidates:
        console.print("[yellow]No jobs passed the rule filter.[/yellow]")
        raise typer.Exit()

    if dry_run:
        for index, (job, rule_evaluation) in enumerate(candidates, start=1):
            console.print(f"[bold]{index}. {job.title}[/bold]")
            console.print(f"{job.company} | {job.location or 'Unknown location'}")
            console.print(f"Rule score: {rule_evaluation.score} | Decision: {rule_evaluation.decision}")
            console.print(str(job.url))
            console.print("-" * 80)
        return

    ranked: list[tuple[JobOffer, RuleEvaluation, AiJobEvaluation]] = []
    try:
        llm_provider = create_llm_provider(provider)
        console.print(f"[bold]LLM provider:[/bold] {llm_provider.name}")
        console.print(f"[bold]Model:[/bold] {llm_provider.model_name}")
        console.print(f"[bold]Timeout:[/bold] {_format_timeout(llm_provider.timeout_seconds)}")
        if isinstance(llm_provider, OllamaLlmProvider):
            console.print(f"[bold]Ollama URL:[/bold] {llm_provider.base_url}")
            with console.status("[bold]Checking Ollama health and model...[/bold]", spinner="dots"):
                llm_provider.check_ready()
            console.print("[green]Ollama ready.[/green]")
        if debug_prompt:
            console.print("[bold]Debug prompts:[/bold] debug/")
        console.print()

        for index, (job, rule_evaluation) in enumerate(candidates, start=1):
            console.print(f"[bold]Evaluation {index}/{len(candidates)}[/bold]")
            console.print(f"[dim]Job:[/dim] {job.title}")
            console.print("[dim]Step:[/dim] preparing prompt")
            system_prompt, user_prompt = build_job_evaluation_prompts(job, candidate_profile)
            prompt_size = _prompt_size(system_prompt, user_prompt)
            if debug_prompt:
                _dump_debug_prompts(
                    index=index,
                    job=job,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
                console.print("[dim]Step:[/dim] prompt dumped to debug/")

            console.print(f"[dim]Provider/model:[/dim] {llm_provider.name} / {llm_provider.model_name}")
            console.print(f"[dim]Timeout:[/dim] {_format_timeout(llm_provider.timeout_seconds)}")
            if verbose:
                console.print(f"[dim]Rule score:[/dim] {rule_evaluation.score} ({rule_evaluation.decision})")
                console.print(f"[dim]Prompt size:[/dim] {prompt_size} bytes")

            started_at = time.perf_counter()
            try:
                console.print("[dim]Step:[/dim] waiting for model response")
                with console.status(
                    f"[bold]Waiting for {llm_provider.name} ({llm_provider.model_name})...[/bold]",
                    spinner="dots",
                ):
                    ai_evaluation = evaluate_job_with_ai(
                        job,
                        candidate_profile,
                        llm_provider,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                    )
                console.print("[dim]Step:[/dim] response parsed and validated")
            except TimeoutError as error:
                console.print("[red]AI ranking timeout[/red]")
                console.print(f"[bold]Provider:[/bold] {llm_provider.name}")
                console.print(f"[bold]Model:[/bold] {llm_provider.model_name}")
                console.print(f"[bold]Timeout:[/bold] {_format_timeout(llm_provider.timeout_seconds)}")
                console.print(f"[bold]Prompt size:[/bold] {prompt_size} bytes")
                console.print(f"[bold]Job title:[/bold] {job.title}")
                raise typer.Exit(code=1) from error

            elapsed = time.perf_counter() - started_at
            console.print(f"[green]Done in {elapsed:.1f}s.[/green]\n")
            ranked.append((job, rule_evaluation, ai_evaluation))
    except RuntimeError as error:
        console.print(f"[red]AI ranking error:[/red] {error}")
        raise typer.Exit(code=1) from error

    ranked.sort(key=lambda item: item[2].fit_score, reverse=True)
    console.print("\n[bold green]Ranked shortlist[/bold green]\n")
    for index, (job, rule_evaluation, ai_evaluation) in enumerate(ranked, start=1):
        _print_ranked_job(index, job, rule_evaluation, ai_evaluation)


if __name__ == "__main__":
    app()
