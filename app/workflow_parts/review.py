from __future__ import annotations

from app.workflow_parts.common import *  # noqa: F401,F403

def rank_offers(
    *,
    profile_path: Path = Path("profiles/default.json"),
    db_path: Path = DEFAULT_DB_PATH,
    limit: int = 10,
    only_recent_days: int | None = None,
    dry_run: bool = False,
    min_score: int | None = 40,
    ranking_mode: RankingMode = "hybrid",
    provider: ProviderName | None = None,
    model: str | None = None,
    preset_id: str = "balanced",
    debug_prompt: bool = False,
    ai_concurrency: int = DEFAULT_AI_RANKING_CONCURRENCY,
    ai_retry_attempts: int = 1,
    ai_retry_backoff: float = 0.0,
    ai_abort_on_error: bool = False,
    progress: ProgressCallback | None = None,
    cancelled: CancellationCheck | None = None,
) -> RankWorkflowResult:
    messages: list[str] = []
    ai_concurrency = _validate_concurrency(ai_concurrency, name="ai_concurrency")
    ai_retry_config = _validate_retry_config(
        ai_retry_attempts,
        ai_retry_backoff,
        name="AI retry",
    )
    _emit(messages, progress, f"Loading profile {profile_path}.")
    _raise_if_cancelled(cancelled)
    candidate_profile = load_profile(profile_path)
    scoring_preset = get_scoring_preset(preset_id, db_path=db_path)
    rule_config = scoring_preset.weights

    provider_name: str | None = None
    model_name: str | None = None
    llm_provider = None
    if ranking_mode != "rules":
        llm_provider = create_llm_provider(provider, model=model or None)
        provider_name = llm_provider.name
        model_name = llm_provider.model_name

    selected_offers = select_screened_offers(
        db_path=db_path,
        provider=provider_name,
        model=model_name,
        profile_path=str(profile_path),
        preset_id=scoring_preset.id,
        min_score=min_score if ranking_mode == "hybrid" else None,
        limit=limit,
        only_recent_days=only_recent_days,
    )
    _emit(messages, progress, f"Selected {len(selected_offers)} scored offers for preset {scoring_preset.id}.")
    _raise_if_cancelled(cancelled)

    evaluated_jobs: list[tuple[StoredOffer, RuleEvaluation]] = []
    for stored_offer in selected_offers:
        _raise_if_cancelled(cancelled)
        evaluated_jobs.append(
            (stored_offer, evaluate_job(stored_offer.job, profile=candidate_profile, config=rule_config))
        )
    if ranking_mode == "hybrid":
        candidates = [
            (stored_offer, evaluation)
            for stored_offer, evaluation in evaluated_jobs
            if min_score is None or evaluation.normalized_score >= min_score
        ]
        prefiltered_count = len(candidates)
        skipped_count = len(evaluated_jobs) - prefiltered_count
        ai_evaluation_count = prefiltered_count
    else:
        candidates = evaluated_jobs
        prefiltered_count = len(candidates) if ranking_mode == "rules" else 0
        skipped_count = 0
        ai_evaluation_count = len(candidates) if ranking_mode == "ai" else 0

    _emit(
        messages,
        progress,
        (
            f"Prefiltered {prefiltered_count}; AI-evaluated {ai_evaluation_count}; "
            f"skipped {skipped_count}."
        ),
    )

    if not candidates or dry_run:
        return RankWorkflowResult(
            profile_path=profile_path,
            db_path=db_path,
            ranking_mode=ranking_mode,
            provider_name=provider_name,
            model_name=model_name,
            selected_count=len(selected_offers),
            prefiltered_count=prefiltered_count,
            ai_evaluation_count=ai_evaluation_count,
            skipped_count=skipped_count,
            saved_count=0,
            run_id=None,
            ranked=[],
            candidates=candidates,
            messages=messages,
        )

    ranked: list[RankedResult] = []
    run_timestamp = datetime.now()
    config_payload = {
        "ranking_mode": ranking_mode,
        "min_score": min_score,
        "limit": limit,
        "only_recent_days": only_recent_days,
        "rule_config": rule_config.model_dump(mode="json"),
        "preset_id": scoring_preset.id,
        "preset_name": scoring_preset.name,
    }
    run_id = create_ranking_run(
        db_path=db_path,
        started_at=run_timestamp.isoformat(timespec="seconds"),
        algorithm=ranking_mode,
        model=model_name,
        profile_path=str(profile_path),
        config=config_payload,
    )

    if ranking_mode == "rules":
        for stored_offer, rule_evaluation in candidates:
            _raise_if_cancelled(cancelled)
            final_decision = make_final_decision(rule_evaluation=rule_evaluation)
            result_payload = ranking_result_payload(
                stored_offer=stored_offer,
                rule_evaluation=rule_evaluation,
                ai_evaluation=None,
                final_decision=final_decision,
            )
            save_ranking(
                db_path=db_path,
                run_id=run_id,
                offer_id=stored_offer.id,
                algorithm=ranking_mode,
                model=model_name,
                profile_path=str(profile_path),
                score=final_decision.final_score,
                recommendation=final_decision.recommendation,
                summary="Rule-only ranking.",
                result=result_payload,
            )
            ranked.append((stored_offer, rule_evaluation, None, final_decision))
    else:
        if llm_provider is None:
            raise RuntimeError("LLM provider was not initialized.")
        _emit(
            messages,
            progress,
            (
                f"Using {llm_provider.name} / {llm_provider.model_name} "
                f"with timeout {format_timeout(llm_provider.timeout_seconds)}; "
                f"AI concurrency {ai_concurrency}."
            ),
        )
        if isinstance(llm_provider, OllamaLlmProvider):
            _emit(messages, progress, "Checking Ollama health and model.")
            _raise_if_cancelled(cancelled)
            llm_provider.check_ready()

        def evaluate_candidate(
            index: int,
            stored_offer: StoredOffer,
            rule_evaluation: RuleEvaluation,
        ) -> tuple[int, StoredOffer, RuleEvaluation, AiJobEvaluation, FinalDecision, float]:
            job = stored_offer.job
            system_prompt, user_prompt = build_job_evaluation_prompts(job, candidate_profile)
            if debug_prompt:
                _dump_debug_prompts(
                    index=index,
                    job=job,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )

            def operation() -> object:
                worker_provider = create_llm_provider(provider, model=model or None)
                return evaluate_job_with_ai(
                    job,
                    candidate_profile,
                    worker_provider,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )

            started_at = time.perf_counter()
            ai_evaluation = _run_with_retries(operation, retry_config=ai_retry_config)
            elapsed = time.perf_counter() - started_at
            final_decision = make_final_decision(
                rule_evaluation=rule_evaluation,
                ai_evaluation=ai_evaluation,  # type: ignore[arg-type]
            )
            return index, stored_offer, rule_evaluation, ai_evaluation, final_decision, elapsed  # type: ignore[return-value]

        completed_ai = 0
        failed_ai = 0
        with ThreadPoolExecutor(max_workers=ai_concurrency) as executor:
            futures = {}
            for index, (stored_offer, rule_evaluation) in enumerate(candidates, start=1):
                _raise_if_cancelled(cancelled)
                _emit(messages, progress, f"Evaluating {index}/{len(candidates)}: {stored_offer.job.title}")
                futures[executor.submit(evaluate_candidate, index, stored_offer, rule_evaluation)] = (
                    index,
                    stored_offer,
                )

            for future in as_completed(futures):
                _raise_if_cancelled(cancelled)
                index, submitted_offer = futures[future]
                try:
                    (
                        _result_index,
                        stored_offer,
                        rule_evaluation,
                        ai_evaluation,
                        final_decision,
                        elapsed,
                    ) = future.result()
                except Exception as error:
                    failed_ai += 1
                    _emit(
                        messages,
                        progress,
                        f"AI evaluation failed {index}/{len(candidates)}: {submitted_offer.job.title}: {error}",
                    )
                    if ai_abort_on_error:
                        raise RuntimeError(f"AI evaluation failed for {submitted_offer.job.title}: {error}") from error
                    continue

                _raise_if_cancelled(cancelled)
                completed_ai += 1
                _emit(messages, progress, f"Model response parsed in {elapsed:.1f}s.")
                result_payload = ranking_result_payload(
                    stored_offer=stored_offer,
                    rule_evaluation=rule_evaluation,
                    ai_evaluation=ai_evaluation,
                    final_decision=final_decision,
                )
                save_ranking(
                    db_path=db_path,
                    run_id=run_id,
                    offer_id=stored_offer.id,
                    algorithm=ranking_mode,
                    model=model_name,
                    profile_path=str(profile_path),
                    score=final_decision.final_score,
                    recommendation=final_decision.recommendation,
                    summary=ai_evaluation.summary,
                    result=result_payload,
                )
                save_ai_review(
                    db_path=db_path,
                    screening_result_id=find_screening_result_id(
                        db_path=db_path,
                        offer_id=stored_offer.id,
                        profile_path=str(profile_path),
                    ),
                    offer_id=stored_offer.id,
                    provider=provider_name,
                    model=model_name,
                    profile_path=str(profile_path),
                    score=final_decision.final_score,
                    recommendation=final_decision.recommendation,
                    summary=ai_evaluation.summary,
                    result=result_payload,
                )
                ranked.append((stored_offer, rule_evaluation, ai_evaluation, final_decision))
                _emit(messages, progress, f"Completed {completed_ai}/{len(candidates)} AI evaluations.")

        if failed_ai:
            skipped_count += failed_ai
            _emit(messages, progress, f"Skipped {failed_ai} failed AI evaluations.")

    ranked.sort(key=lambda item: item[3].final_score, reverse=True)
    _emit(messages, progress, f"Saved {len(ranked)} rankings.")
    return RankWorkflowResult(
        profile_path=profile_path,
        db_path=db_path,
        ranking_mode=ranking_mode,
        provider_name=provider_name,
        model_name=model_name,
        selected_count=len(selected_offers),
        prefiltered_count=prefiltered_count,
        ai_evaluation_count=ai_evaluation_count,
        skipped_count=skipped_count,
        saved_count=len(ranked),
        run_id=run_id,
        ranked=ranked,
        candidates=candidates,
        messages=messages,
    )
