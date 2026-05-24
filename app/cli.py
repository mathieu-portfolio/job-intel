from __future__ import annotations

import json
import re
import time
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Literal

import requests
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.ai.decision import make_final_decision
from app.ai.evaluator import build_job_evaluation_prompts, evaluate_job_with_ai
from app.filtering.rules import evaluate_job, filter_jobs, load_rule_scoring_config
from app.llm.factory import ProviderName, create_llm_provider
from app.llm.ollama_client import OllamaLlmProvider
from app.models.evaluation import AiJobEvaluation, FinalDecision, RuleEvaluation, WeightedTermMatch
from app.models.job import JobOffer
from app.sources.adzuna import fetch_adzuna
from app.sources.arbeitnow import fetch_arbeitnow
from app.storage.files import load_profile, save_jobs
from app.storage.sqlite import (
    DEFAULT_DB_PATH,
    StoredOffer,
    create_ranking_run,
    save_ranking,
    select_unranked_offers,
    upsert_offers,
)


app = typer.Typer(help="Fetch and filter technical job offers.", no_args_is_help=True)
console = Console()
RankingMode = Literal["rules", "ai", "hybrid"]
RankedResult = tuple[StoredOffer, RuleEvaluation, AiJobEvaluation | None, FinalDecision]


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


def _format_term_matches(matches: list[WeightedTermMatch]) -> str:
    if not matches:
        return "none"
    return ", ".join(f"{match.term} ({match.weight:+d})" for match in matches)


def _ranking_result_payload(
    *,
    stored_offer: StoredOffer,
    rule_evaluation: RuleEvaluation,
    ai_evaluation: AiJobEvaluation | None,
    final_decision: FinalDecision,
) -> dict[str, object]:
    return {
        "offer_id": stored_offer.id,
        "job": stored_offer.job.model_dump(mode="json"),
        "rule_evaluation": rule_evaluation.model_dump(mode="json"),
        "raw_ai_evaluation": (
            ai_evaluation.model_dump(mode="json") if ai_evaluation is not None else None
        ),
        "final_decision": final_decision.model_dump(mode="json"),
    }


def _save_ranked_results(
    *,
    ranked: list[RankedResult],
    timestamp: datetime,
    provider_name: str | None,
    model_name: str | None,
    ranking_mode: RankingMode,
    profile_path: Path,
    db_path: Path,
    weights_path: Path | None,
) -> Path:
    output_dir = Path("data/ranked")
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp_slug = timestamp.strftime("%Y-%m-%d_%H-%M-%S")
    output_path = output_dir / f"ranked_{timestamp_slug}.json"
    suffix = 2
    while output_path.exists():
        output_path = output_dir / f"ranked_{timestamp_slug}_{suffix}.json"
        suffix += 1
    payload = {
        "run_metadata": {
            "timestamp": timestamp.isoformat(timespec="seconds"),
            "provider": provider_name,
            "model": model_name,
            "ranking_mode": ranking_mode,
            "profile_path": str(profile_path),
            "db_path": str(db_path),
            "weights_path": str(weights_path) if weights_path else None,
        },
        "results": [
            _ranking_result_payload(
                stored_offer=stored_offer,
                rule_evaluation=rule_evaluation,
                ai_evaluation=ai_evaluation,
                final_decision=final_decision,
            )
            for stored_offer, rule_evaluation, ai_evaluation, final_decision in ranked
        ],
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


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
    db: Path = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path."),
    export_json: bool = typer.Option(False, "--export-json", help="Also export fetched jobs to JSON."),
    min_score: int = typer.Option(10, help="Minimum rule score to print."),
    limit: int = typer.Option(20, help="Maximum number of matches to print."),
) -> None:
    """Fetch jobs from one source, upsert SQLite offers, and print a shortlist."""

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

    stats = upsert_offers(jobs, db_path=db)
    if export_json:
        save_jobs(jobs)
    matches = filter_jobs(jobs, min_score=min_score)

    console.print(f"[bold]Fetched:[/bold] {stats.fetched} jobs from {source}")
    console.print(f"[bold]Database:[/bold] {db}")
    console.print(f"[green]Inserted:[/green] {stats.inserted} | [yellow]Updated:[/yellow] {stats.updated}")
    if export_json:
        console.print("[bold]JSON export:[/bold] data/normalized/latest_jobs.json")
    console.print(f"[green]Matched:[/green] {len(matches)} jobs with score >= {min_score}\n")

    for index, (job, evaluation) in enumerate(matches[:limit], start=1):
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
            f"[bold]Seniority penalty[/bold] {final_decision.seniority_mismatch_penalty}",
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
            f"seniority {ai_evaluation.seniority_fit_score}, "
            f"learning {ai_evaluation.learning_potential_score}, "
            f"portfolio {ai_evaluation.portfolio_alignment_score}, "
            f"wording risk {ai_evaluation.wording_risk_score}"
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
    min_score: int = typer.Option(10, help="Minimum cheap rule score before AI evaluation."),
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
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print detailed evaluation progress."),
    debug_prompt: bool = typer.Option(False, "--debug-prompt", help="Write evaluation prompts into debug/."),
    export_json: bool = typer.Option(False, "--export-json", help="Also export this ranking run to data/ranked/."),
) -> None:
    """Rank unranked SQLite offers against a candidate profile."""

    try:
        candidate_profile = load_profile(profile)
    except FileNotFoundError as error:
        console.print(f"[red]Missing file:[/red] {error.filename}")
        raise typer.Exit(code=1) from error
    except ValueError as error:
        console.print(f"[red]Invalid input:[/red] {error}")
        raise typer.Exit(code=1) from error
    try:
        rule_config = load_rule_scoring_config(weights_path)
    except RuntimeError as error:
        console.print(f"[red]Rule scoring configuration error:[/red] {error}")
        raise typer.Exit(code=1) from error

    provider_name: str | None = None
    model_name: str | None = None
    llm_provider = None
    if ranking_mode != "rules":
        try:
            llm_provider = create_llm_provider(provider)
            provider_name = llm_provider.name
            model_name = llm_provider.model_name
        except RuntimeError as error:
            console.print(f"[red]AI ranking error:[/red] {error}")
            raise typer.Exit(code=1) from error

    algorithm = ranking_mode
    selected_offers = select_unranked_offers(
        db_path=db,
        algorithm=algorithm,
        model=model_name,
        profile_path=str(profile),
        limit=limit,
        only_recent_days=only_recent_days,
    )

    if ranking_mode == "ai":
        evaluated_jobs = [
            (stored_offer, evaluate_job(stored_offer.job, profile=candidate_profile, config=rule_config))
            for stored_offer in selected_offers
        ]
        candidates = evaluated_jobs
        prefilter_description = f"No rule prefilter in ai mode; evaluating first {len(candidates)} jobs"
    else:
        evaluated_jobs = [
            (stored_offer, evaluate_job(stored_offer.job, profile=candidate_profile, config=rule_config))
            for stored_offer in selected_offers
        ]
        candidates = evaluated_jobs
        eligible_count = sum(1 for _, evaluation in candidates if evaluation.score >= min_score)
        prefilter_description = (
            f"Rule prefilter: {eligible_count}/{len(candidates)} jobs with score >= {min_score}"
        )

    console.print(f"[bold]Profile:[/bold] {profile}")
    console.print(f"[bold]Database:[/bold] {db}")
    console.print(f"[bold]Unranked offers selected:[/bold] {len(selected_offers)}")
    console.print(f"[bold]Ranking mode:[/bold] {ranking_mode}")
    console.print(f"[bold]{prefilter_description}[/bold]\n")

    if not candidates:
        console.print("[yellow]No unranked offers matched this ranking request.[/yellow]")
        raise typer.Exit()

    if dry_run:
        for index, (stored_offer, rule_evaluation) in enumerate(candidates, start=1):
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

    ranked: list[RankedResult] = []
    run_timestamp = datetime.now()
    config_payload = {
        "ranking_mode": ranking_mode,
        "min_score": min_score,
        "limit": limit,
        "only_recent_days": only_recent_days,
        "weights_path": str(weights_path) if weights_path else None,
        "rule_config": rule_config.model_dump(mode="json"),
    }
    run_id = create_ranking_run(
        db_path=db,
        started_at=run_timestamp.isoformat(timespec="seconds"),
        algorithm=algorithm,
        model=model_name,
        profile_path=str(profile),
        config=config_payload,
    )

    if ranking_mode == "rules":
        for stored_offer, rule_evaluation in candidates:
            final_decision = make_final_decision(rule_evaluation=rule_evaluation)
            result_payload = _ranking_result_payload(
                stored_offer=stored_offer,
                rule_evaluation=rule_evaluation,
                ai_evaluation=None,
                final_decision=final_decision,
            )
            save_ranking(
                db_path=db,
                run_id=run_id,
                offer_id=stored_offer.id,
                algorithm=algorithm,
                model=model_name,
                profile_path=str(profile),
                score=final_decision.final_score,
                recommendation=final_decision.recommendation,
                summary="Rule-only ranking.",
                result=result_payload,
            )
            ranked.append((stored_offer, rule_evaluation, None, final_decision))
    else:
        try:
            if llm_provider is None:
                raise RuntimeError("LLM provider was not initialized.")
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

            for index, (stored_offer, rule_evaluation) in enumerate(candidates, start=1):
                job = stored_offer.job
                if ranking_mode == "hybrid" and rule_evaluation.score < min_score:
                    final_decision = make_final_decision(rule_evaluation=rule_evaluation)
                    result_payload = _ranking_result_payload(
                        stored_offer=stored_offer,
                        rule_evaluation=rule_evaluation,
                        ai_evaluation=None,
                        final_decision=final_decision,
                    )
                    save_ranking(
                        db_path=db,
                        run_id=run_id,
                        offer_id=stored_offer.id,
                        algorithm=algorithm,
                        model=model_name,
                        profile_path=str(profile),
                        score=final_decision.final_score,
                        recommendation=final_decision.recommendation,
                        summary="Skipped by hybrid rule prefilter.",
                        result=result_payload,
                    )
                    ranked.append((stored_offer, rule_evaluation, None, final_decision))
                    if verbose:
                        console.print(
                            f"[dim]Skipping AI for {job.title}: rule score "
                            f"{rule_evaluation.score} < {min_score}.[/dim]"
                        )
                    continue

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
                    console.print(
                        f"[dim]Rule score:[/dim] {rule_evaluation.score} "
                        f"({rule_evaluation.normalized_score}/100, {rule_evaluation.decision})"
                    )
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
                final_decision = make_final_decision(
                    rule_evaluation=rule_evaluation,
                    ai_evaluation=ai_evaluation,
                )
                result_payload = _ranking_result_payload(
                    stored_offer=stored_offer,
                    rule_evaluation=rule_evaluation,
                    ai_evaluation=ai_evaluation,
                    final_decision=final_decision,
                )
                save_ranking(
                    db_path=db,
                    run_id=run_id,
                    offer_id=stored_offer.id,
                    algorithm=algorithm,
                    model=model_name,
                    profile_path=str(profile),
                    score=final_decision.final_score,
                    recommendation=final_decision.recommendation,
                    summary=ai_evaluation.summary,
                    result=result_payload,
                )
                ranked.append((stored_offer, rule_evaluation, ai_evaluation, final_decision))
        except RuntimeError as error:
            console.print(f"[red]AI ranking error:[/red] {error}")
            raise typer.Exit(code=1) from error

    ranked.sort(key=lambda item: item[3].final_score, reverse=True)
    console.print("\n[bold green]Ranked shortlist[/bold green]\n")
    for index, (stored_offer, rule_evaluation, ai_evaluation, final_decision) in enumerate(ranked, start=1):
        _print_ranked_job(index, stored_offer.job, rule_evaluation, ai_evaluation, final_decision)
    console.print(f"\n[bold]Saved ranking run:[/bold] {run_id} in {db}")
    if export_json:
        output_path = _save_ranked_results(
            ranked=ranked,
            timestamp=run_timestamp,
            provider_name=provider_name,
            model_name=model_name,
            ranking_mode=ranking_mode,
            profile_path=profile,
            db_path=db,
            weights_path=weights_path,
        )
    console.print(f"[bold]JSON export:[/bold] {output_path}")


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
