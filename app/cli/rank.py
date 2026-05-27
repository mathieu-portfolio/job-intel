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

def rank(
    profile: Path | None = typer.Option(None, help="Candidate profile JSON path. Defaults to the first discovered profile by (order, id)."),
    db: Path = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path."),
    limit: int = typer.Option(10, min=1, help="Maximum number of jobs to evaluate."),
    only_recent_days: int | None = typer.Option(None, min=1, help="Only rank offers seen or published in the last N days."),
    dry_run: bool = typer.Option(False, help="Print jobs that would be evaluated without calling an LLM."),
    min_score: int = typer.Option(40, help="Minimum calibrated rule score for hybrid ranking only."),
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
    ai_concurrency: int = typer.Option(1, min=1, help="Maximum AI reviews to evaluate in parallel."),
    ai_retry_attempts: int = typer.Option(1, min=1, help="AI evaluation retry attempts per offer."),
    ai_retry_backoff: float = typer.Option(0.0, min=0.0, help="AI retry backoff seconds, multiplied by attempt."),
    ai_abort_on_error: bool = typer.Option(False, help="Abort ranking when one AI evaluation fails."),
) -> None:
    """Rank unranked SQLite offers against a candidate profile."""

    try:
        result = rank_offers(
            profile_id=str(profile),
            db_path=db,
            limit=limit,
            only_recent_days=only_recent_days,
            dry_run=dry_run,
            min_score=min_score,
            ranking_mode=ranking_mode,
            provider=provider,
            preset_id=preset,
            debug_prompt=debug_prompt,
            ai_concurrency=ai_concurrency,
            ai_retry_attempts=ai_retry_attempts,
            ai_retry_backoff=ai_retry_backoff,
            ai_abort_on_error=ai_abort_on_error,
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
