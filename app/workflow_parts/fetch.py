from __future__ import annotations

from app.workflow_parts.common import *  # noqa: F401,F403

def fetch_offers(
    *,
    source: FetchSource = "arbeitnow",
    page: int = 1,
    pages: int = 1,
    new_offers: int | None = None,
    max_pages: int | None = None,
    max_seen_pages: int = 5,
    query: str = "c++ simulation",
    country: str = "fr",
    where: str | None = None,
    profile_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    min_score: int | None = None,
    explored_capacity: int = DEFAULT_EXPLORED_CAPACITY,
    unranked_capacity: int = DEFAULT_UNRANKED_CAPACITY,
    ranked_capacity: int = DEFAULT_RANKED_CAPACITY,
    exploration_mode: ExplorationMode = "safe",
    use_profile_queries: bool = True,
    fetch_concurrency: int = DEFAULT_FETCH_CONCURRENCY,
    provider_retry_attempts: int = 1,
    provider_retry_backoff: float = 0.0,
    progress: ProgressCallback | None = None,
    cancelled: CancellationCheck | None = None,
) -> FetchWorkflowResult:
    messages: list[str] = []
    timing = {
        "provider_fetch": 0.0,
        "explored_lookup": 0.0,
        "scoring": 0.0,
        "offer_upsert": 0.0,
        "score_persistence": 0.0,
        "screened_persistence": 0.0,
        "explored_persistence": 0.0,
    }
    total_started_at = time.perf_counter()
    if pages < 1:
        raise ValueError("pages must be at least 1.")
    if new_offers is not None and new_offers < 1:
        raise ValueError("new_offers must be at least 1.")
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be at least 1.")
    if max_seen_pages < 1:
        raise ValueError("max_seen_pages must be at least 1.")
    if exploration_mode not in {"safe", "normal", "fast_backfill"}:
        raise ValueError("exploration_mode must be safe, normal, or fast_backfill.")
    fetch_concurrency = _validate_concurrency(fetch_concurrency, name="fetch_concurrency")
    provider_retry_config = _validate_retry_config(
        provider_retry_attempts,
        provider_retry_backoff,
        name="provider retry",
    )
    profile_ref = profile_id
    profile_id = profile_id_from_value(profile_ref)
    candidate_profile = load_profile(profile_ref)
    search_requests = iter_profile_search_requests(
        candidate_profile,
        source,
        query=query,
        where=where,
        use_profile_queries=use_profile_queries,
    )
    if len(search_requests) > 1:
        planned_pages_per_request = max_pages if new_offers is not None and max_pages is not None else pages
        _emit(
            messages,
            progress,
            (
                f"Fetch plan: {len(search_requests)} profile search requests for {source}; "
                f"up to {planned_pages_per_request} pages/request; "
                f"up to {len(search_requests) * planned_pages_per_request} provider page requests. "
                + "; ".join(_format_search_request(request) for request in search_requests[:8])
                + ("; ..." if len(search_requests) > 8 else "")
            ),
        )
        aggregate_stats = UpsertStats(fetched=0, inserted=0, updated=0)
        aggregate_matches: list[tuple[JobOffer, RuleEvaluation]] = []
        aggregate_messages = messages
        aggregate_prune: PruneStats | None = None
        aggregate_deleted_explored = 0
        aggregate_deleted_unranked = 0
        aggregate_deleted_ranked = 0
        aggregate_summaries: list[FetchRequestSummary] = []
        aggregate_provider_keys: set[tuple[str, str]] = set()
        duplicate_provider_rows_across_requests = 0
        for search_request in search_requests:
            remaining_new_offers = (
                None
                if new_offers is None
                else max(0, new_offers - aggregate_stats.newly_explored)
            )
            if remaining_new_offers == 0:
                break
            result = fetch_offers(
                source=source,
                page=page,
                pages=pages,
                new_offers=remaining_new_offers,
                max_pages=max_pages,
                max_seen_pages=max_seen_pages,
                query=search_request.query,
                country=country,
                where=search_request.where,
                profile_id=profile_ref,
                db_path=db_path,
                min_score=min_score,
                explored_capacity=explored_capacity,
                unranked_capacity=unranked_capacity,
                ranked_capacity=ranked_capacity,
                exploration_mode=exploration_mode,
                use_profile_queries=False,
                fetch_concurrency=fetch_concurrency,
                provider_retry_attempts=provider_retry_attempts,
                provider_retry_backoff=provider_retry_backoff,
                progress=progress,
                cancelled=cancelled,
            )
            request_duplicate_keys = aggregate_provider_keys.intersection(result.provider_keys)
            duplicate_provider_rows_across_requests += len(request_duplicate_keys)
            aggregate_provider_keys.update(result.provider_keys)
            aggregate_summaries.extend(result.request_summaries)
            aggregate_stats = UpsertStats(
                fetched=aggregate_stats.fetched + result.stats.fetched,
                inserted=aggregate_stats.inserted + result.stats.inserted,
                updated=aggregate_stats.updated + result.stats.updated,
                skipped_existing=aggregate_stats.skipped_existing + result.stats.skipped_existing,
                pages_scanned=aggregate_stats.pages_scanned + result.stats.pages_scanned,
                explored=aggregate_stats.explored + result.stats.explored,
                newly_explored=aggregate_stats.newly_explored + result.stats.newly_explored,
                already_seen=aggregate_stats.already_seen + result.stats.already_seen,
                filtered_out=aggregate_stats.filtered_out + result.stats.filtered_out,
                errors=aggregate_stats.errors + result.stats.errors,
            )
            aggregate_matches.extend(result.matches)
            aggregate_messages.extend(result.messages)
            aggregate_prune = result.prune_stats
            aggregate_deleted_explored += result.prune_stats.deleted_explored
            aggregate_deleted_unranked += result.prune_stats.deleted_unranked
            aggregate_deleted_ranked += result.prune_stats.deleted_ranked

        if aggregate_prune is None:
            aggregate_prune = prune_storage(
                db_path,
                explored_capacity=explored_capacity,
                unranked_capacity=unranked_capacity,
                ranked_capacity=ranked_capacity,
            )
        aggregate_prune = PruneStats(
            deleted_explored=aggregate_deleted_explored,
            deleted_unranked=aggregate_deleted_unranked,
            deleted_ranked=aggregate_deleted_ranked,
            before=aggregate_prune.before,
            after=aggregate_prune.after,
        )
        screened_total = aggregate_stats.inserted + aggregate_stats.updated
        requests_count = max(len(aggregate_summaries), 1)
        _emit(
            aggregate_messages,
            progress,
            (
                "Fetch plan summary: "
                f"requests {len(aggregate_summaries)}; pages {aggregate_stats.pages_scanned}; "
                f"provider rows {aggregate_stats.fetched}; unique provider rows {len(aggregate_provider_keys)}; "
                f"cross-query duplicates {duplicate_provider_rows_across_requests}; "
                f"screened {screened_total}; screened/request {screened_total / requests_count:.1f}."
            ),
        )
        return FetchWorkflowResult(
            source=source,
            db_path=db_path,
            stats=aggregate_stats,
            prune_stats=aggregate_prune,
            matched_count=len(aggregate_matches),
            matches=aggregate_matches,
            messages=aggregate_messages,
            request_summaries=aggregate_summaries,
            provider_keys=frozenset(aggregate_provider_keys),
        )

    query = search_requests[0].query
    where = search_requests[0].where

    target_new_offers = new_offers
    page_limit = max_pages if target_new_offers is not None else pages
    if page_limit is None:
        page_limit = pages

    _emit(
        messages,
        progress,
        (
            f"Fetching jobs from {source}; "
            f"target {target_new_offers or 'page scan'} newly explored offers; max pages {page_limit}."
        ),
    )
    _raise_if_cancelled(cancelled)

    fetched = 0
    inserted = 0
    updated = 0
    already_seen = 0
    newly_explored = 0
    filtered_out = 0
    errors = 0
    pages_scanned = 0
    consecutive_seen_pages = 0
    matches: list[tuple[JobOffer, RuleEvaluation]] = []
    provider_keys: set[tuple[str, str]] = set()
    duplicate_provider_rows = 0
    screening_threshold = min_score if min_score is not None else candidate_profile.screening_threshold
    init_db(db_path)
    enabled_presets = list_scoring_presets(db_path, enabled_only=True)
    if not enabled_presets:
        raise RuntimeError("No enabled scoring presets are configured.")
    precompute_rule_matching(candidate_profile, [preset.weights for preset in enabled_presets])

    scope_payload = _exploration_scope_payload(
        source=source,
        query=query,
        country=country,
        where=where,
        profile_id=profile_id,
        min_score=min_score,
    )
    scope_key = _exploration_scope_key(scope_payload)
    metadata = get_exploration_metadata(db_path=db_path, scope_key=scope_key)
    previous_newest_id = metadata.newest_id if metadata else None
    previous_oldest_id = metadata.oldest_id if metadata else None
    previous_last_explored_page = metadata.last_explored_page if metadata else None
    fast_backfill_active = (
        exploration_mode == "fast_backfill"
        and bool(previous_newest_id)
        and bool(previous_oldest_id)
        and previous_last_explored_page is not None
        and previous_last_explored_page > 1
    )
    if exploration_mode == "fast_backfill" and not fast_backfill_active:
        _emit(messages, progress, "Fast backfill metadata missing or incomplete; using normal exploration.")
    elif fast_backfill_active:
        _emit(
            messages,
            progress,
            (
                "Fast backfill enabled; processing new top offers before "
                f"{previous_newest_id}, then jumping near page {previous_last_explored_page}."
            ),
        )

    first_seen_identity: str | None = None
    last_seen_identity: str | None = None
    last_scanned_page: int | None = None
    current_page = 1 if fast_backfill_active else page
    fast_phase = "top" if fast_backfill_active else "normal"
    skip_pages_scanned = 0

    def fetch_provider_page(fetch_page: int) -> tuple[list[JobOffer], float]:
        def operation() -> object:
            started_at = time.perf_counter()
            if source == "arbeitnow":
                return fetch_arbeitnow(page=fetch_page), time.perf_counter() - started_at
            if source == "adzuna":
                return fetch_adzuna(query=query, country=country, where=where, page=fetch_page), time.perf_counter() - started_at
            raise ValueError(f"Unsupported source: {source}")

        return _run_with_retries(operation, retry_config=provider_retry_config)  # type: ignore[return-value]

    def process_jobs_batch(jobs: list[JobOffer], *, page_counts: dict[str, int]) -> bool:
        nonlocal inserted
        nonlocal updated
        nonlocal already_seen
        nonlocal newly_explored
        nonlocal filtered_out
        nonlocal errors
        if not jobs:
            return False

        batch_started_at = time.perf_counter()
        with open_connection(db_path) as connection:
            lookup_started_at = time.perf_counter()
            explored_identities = has_explored_offers_batch(
                connection,
                jobs[0].source,
                [(job.source_id, str(job.url)) for job in jobs],
            )
            timing["explored_lookup"] += time.perf_counter() - lookup_started_at

            jobs_to_score: list[JobOffer] = []
            explored_records: list[tuple[JobOffer, str, str | None, bool]] = []
            stop_after_page = False
            for job in jobs:
                _raise_if_cancelled(cancelled)
                canonical_url = str(job.url)
                if (job.source_id, canonical_url) in explored_identities:
                    already_seen += 1
                    page_counts["already_seen"] += 1
                    continue

                newly_explored += 1
                page_counts["newly_explored"] += 1
                if target_new_offers is not None:
                    _emit(
                        messages,
                        progress,
                        f"Processed {newly_explored}/{target_new_offers} newly explored offers.",
                    )

                if not job.description.strip():
                    filtered_out += 1
                    explored_records.append((job, "filtered_out", "missing_description", False))
                else:
                    jobs_to_score.append(job)

                if target_new_offers is not None and newly_explored >= target_new_offers:
                    stop_after_page = True
                    break

            lookup_started_at = time.perf_counter()
            existing_offer_ids = find_existing_offer_ids_batch(connection, jobs_to_score)
            existing_url_ids = find_existing_offer_ids_by_url_batch(
                connection,
                [str(job.url) for job in jobs_to_score],
            )
            timing["explored_lookup"] += time.perf_counter() - lookup_started_at

            score_rows: list[tuple[int, str, RuleEvaluation]] = []
            screening_rows: list[tuple[int, str, RuleEvaluation, int]] = []
            upsert_jobs: list[JobOffer] = []
            profile_matches_by_url: dict[str, RuleEvaluation] = {}
            selected_evaluation_by_url: dict[str, RuleEvaluation] = {}
            selected_preset = next((preset for preset in enabled_presets if preset.id == "balanced"), enabled_presets[0])

            for job in jobs_to_score:
                canonical_url = str(job.url)
                try:
                    scoring_started_at = time.perf_counter()
                    profile_match = evaluate_job_profile_match(job, profile=candidate_profile, config=selected_preset.weights)
                    evaluation = score_profile_match(profile_match, selected_preset.weights)
                    timing["scoring"] += time.perf_counter() - scoring_started_at
                    profile_matches_by_url[canonical_url] = profile_match
                    selected_evaluation_by_url[canonical_url] = evaluation

                    existing_offer_id = existing_offer_ids.get(canonical_url)
                    if existing_offer_id is not None:
                        score_rows.append((existing_offer_id, profile_id, profile_match))
                        screening_rows.append(
                            (existing_offer_id, profile_id, evaluation, screening_threshold)
                        )
                        if canonical_url in existing_url_ids:
                            upsert_jobs.append(job)
                            explored_records.append((job, "updated", "already_in_offers", False))
                        else:
                            explored_records.append((job, "duplicate", "already_in_offers", False))
                        continue

                    if evaluation.normalized_score < screening_threshold:
                        filtered_out += 1
                        explored_records.append((job, "filtered_out", "rule_filter_failed", False))
                        continue

                    upsert_jobs.append(job)
                    explored_records.append((job, "inserted", None, False))
                except Exception as error:
                    errors += 1
                    explored_records.append((job, "error", str(error), False))

            upsert_started_at = time.perf_counter()
            upsert_stats, upserted_offer_ids = upsert_offers_batch(connection, upsert_jobs)
            timing["offer_upsert"] += time.perf_counter() - upsert_started_at
            inserted += upsert_stats.inserted
            updated += upsert_stats.updated

            for job in upsert_jobs:
                canonical_url = str(job.url)
                offer_id = upserted_offer_ids.get(canonical_url)
                if offer_id is None:
                    continue
                profile_match = profile_matches_by_url[canonical_url]
                evaluation = selected_evaluation_by_url[canonical_url]
                score_rows.append((offer_id, profile_id, profile_match))
                screening_rows.append((offer_id, profile_id, evaluation, screening_threshold))
                if canonical_url not in existing_offer_ids:
                    matches.append((job, evaluation))

            score_started_at = time.perf_counter()
            save_offer_scores_batch(connection, score_rows)
            timing["score_persistence"] += time.perf_counter() - score_started_at

            screened_started_at = time.perf_counter()
            save_screening_results_batch(connection, screening_rows)
            timing["screened_persistence"] += time.perf_counter() - screened_started_at

            explored_started_at = time.perf_counter()
            record_explored_jobs_batch(connection, explored_records)
            timing["explored_persistence"] += time.perf_counter() - explored_started_at

        _emit(
            messages,
            progress,
            (
                f"Processed page batch: {len(jobs)} provider rows in "
                f"{time.perf_counter() - batch_started_at:.2f}s."
            ),
        )
        return stop_after_page

    if target_new_offers is None and not fast_backfill_active and fetch_concurrency > 1:
        page_numbers = list(range(page, page + page_limit))
        page_results: dict[int, list[JobOffer]] = {}
        failed_pages: set[int] = set()
        _emit(
            messages,
            progress,
            f"Fetching provider pages with concurrency {fetch_concurrency}.",
        )
        with ThreadPoolExecutor(max_workers=fetch_concurrency) as executor:
            futures = {
                executor.submit(fetch_provider_page, fetch_page): fetch_page
                for fetch_page in page_numbers
            }
            for future in as_completed(futures):
                _raise_if_cancelled(cancelled)
                completed_page = futures[future]
                try:
                    page_jobs, page_elapsed = future.result()
                    page_results[completed_page] = page_jobs
                    timing["provider_fetch"] += page_elapsed
                    _emit(
                        messages,
                        progress,
                        f"Fetched provider page {completed_page}: {len(page_results[completed_page])} offers.",
                    )
                except Exception as error:
                    errors += 1
                    failed_pages.add(completed_page)
                    page_results[completed_page] = []
                    _emit(messages, progress, f"Provider page {completed_page} failed: {error}")

        for fetched_page in page_numbers:
            _raise_if_cancelled(cancelled)
            jobs = page_results.get(fetched_page, [])
            if fetched_page in failed_pages:
                pages_scanned += 1
                continue

            pages_scanned += 1
            last_scanned_page = fetched_page
            fetched += len(jobs)
            for job in jobs:
                provider_key = _offer_provider_key(job)
                if provider_key in provider_keys:
                    duplicate_provider_rows += 1
                provider_keys.add(provider_key)
            _emit(messages, progress, f"Scanned page {fetched_page}: {len(jobs)} offers.")
            if not jobs:
                _emit(messages, progress, "Provider returned no more offers.")
                break

            if first_seen_identity is None:
                first_seen_identity = _offer_exploration_id(jobs[0])
            last_seen_identity = _offer_exploration_id(jobs[-1])

            page_counts = {"already_seen": 0, "newly_explored": 0}
            process_jobs_batch(jobs, page_counts=page_counts)
            if page_counts["already_seen"] == len(jobs) and page_counts["newly_explored"] == 0:
                consecutive_seen_pages += 1
                if consecutive_seen_pages >= max_seen_pages:
                    _emit(
                        messages,
                        progress,
                        f"Stopped after {consecutive_seen_pages} consecutive pages with only already-seen offers.",
                    )
                    break
            else:
                consecutive_seen_pages = 0

    while pages_scanned < page_limit and not (
        target_new_offers is None and not fast_backfill_active and fetch_concurrency > 1
    ):
        _raise_if_cancelled(cancelled)
        try:
            jobs, provider_elapsed = fetch_provider_page(current_page)
            timing["provider_fetch"] += provider_elapsed
        except Exception as error:
            errors += 1
            pages_scanned += 1
            _emit(messages, progress, f"Provider page {current_page} failed: {error}")
            current_page += 1
            continue

        pages_scanned += 1
        last_scanned_page = current_page
        fetched += len(jobs)
        for job in jobs:
            provider_key = _offer_provider_key(job)
            if provider_key in provider_keys:
                duplicate_provider_rows += 1
            provider_keys.add(provider_key)
        _emit(messages, progress, f"Scanned page {current_page}: {len(jobs)} offers.")
        if not jobs:
            _emit(messages, progress, "Provider returned no more offers.")
            break

        if first_seen_identity is None:
            first_seen_identity = _offer_exploration_id(jobs[0])
        last_seen_identity = _offer_exploration_id(jobs[-1])

        page_counts = {"already_seen": 0, "newly_explored": 0}
        jump_page: int | None = None
        jobs_to_process: list[JobOffer] = []
        for index, job in enumerate(jobs):
            _raise_if_cancelled(cancelled)
            identity = _offer_exploration_id(job)
            if fast_phase == "top" and identity == previous_newest_id:
                jump_page = max(1, int(previous_last_explored_page or 1) - 1)
                fast_phase = "skip"
                skip_pages_scanned = 0
                _emit(messages, progress, f"Reached previous newest offer; jumping to page {jump_page}.")
                break
            if fast_phase == "skip":
                if identity == previous_oldest_id:
                    fast_phase = "normal"
                    _emit(messages, progress, "Reached previous oldest offer; resuming normal deduplication.")
                    jobs_to_process.extend(jobs[index + 1:])
                    break
                continue
            jobs_to_process.append(job)

        process_jobs_batch(jobs_to_process, page_counts=page_counts)

        if fast_phase == "skip":
            skip_pages_scanned += 1
            if skip_pages_scanned >= FAST_BACKFILL_SKIP_PAGE_LIMIT:
                fast_phase = "normal"
                _emit(
                    messages,
                    progress,
                    "Previous oldest offer was not found quickly; resuming normal deduplication.",
                )

        if page_counts["already_seen"] == len(jobs) and page_counts["newly_explored"] == 0:
            consecutive_seen_pages += 1
            if consecutive_seen_pages >= max_seen_pages:
                _emit(
                    messages,
                    progress,
                    f"Stopped after {consecutive_seen_pages} consecutive pages with only already-seen offers.",
                )
                break
        else:
            consecutive_seen_pages = 0
        if target_new_offers is not None and newly_explored >= target_new_offers:
            _emit(messages, progress, f"Processed {newly_explored} newly explored offers.")
            break
        if jump_page is not None:
            current_page = jump_page
            continue
        current_page += 1

    stats = UpsertStats(
        fetched=fetched,
        inserted=inserted,
        updated=updated,
        skipped_existing=already_seen,
        pages_scanned=pages_scanned,
        explored=newly_explored,
        newly_explored=newly_explored,
        already_seen=already_seen,
        filtered_out=filtered_out,
        errors=errors,
    )
    _emit(
        messages,
        progress,
        (
            f"Pages {stats.pages_scanned}; provider rows {stats.fetched}; "
            f"newly explored {stats.newly_explored}; "
            f"already seen {stats.already_seen}; screened out {stats.filtered_out}; "
            f"screened {stats.inserted + stats.updated}; inserted {stats.inserted}; "
            f"updated {stats.updated}; errors {stats.errors}."
        ),
    )
    request_summary = FetchRequestSummary(
        query=query,
        where=where,
        pages_scanned=stats.pages_scanned,
        provider_rows=stats.fetched,
        unique_provider_rows=len(provider_keys),
        duplicate_provider_rows=duplicate_provider_rows,
        newly_explored=stats.newly_explored,
        already_seen=stats.already_seen,
        filtered_out=stats.filtered_out,
        screened=stats.inserted + stats.updated,
    )
    _emit(
        messages,
        progress,
        (
            "Fetch request summary: "
            f"query={request_summary.query or '<broad>'}; "
            f"where={request_summary.where or '<any>'}; "
            f"pages={request_summary.pages_scanned}; rows={request_summary.provider_rows}; "
            f"unique={request_summary.unique_provider_rows}; duplicate_rows={request_summary.duplicate_provider_rows}; "
            f"new={request_summary.newly_explored}; screened={request_summary.screened}."
        ),
    )
    total_elapsed = max(time.perf_counter() - total_started_at, 0.001)
    _emit(
        messages,
        progress,
        (
            "Fetch timing: "
            f"provider fetch {timing['provider_fetch']:.2f}s; "
            f"explored lookup {timing['explored_lookup']:.2f}s; "
            f"scoring {timing['scoring']:.2f}s; "
            f"offer upsert {timing['offer_upsert']:.2f}s; "
            f"score persistence {timing['score_persistence']:.2f}s; "
            f"screened persistence {timing['screened_persistence']:.2f}s; "
            f"explored persistence {timing['explored_persistence']:.2f}s; "
            f"{stats.fetched / total_elapsed:.1f} provider offers/sec."
        ),
    )
    if first_seen_identity and last_seen_identity and last_scanned_page is not None:
        _raise_if_cancelled(cancelled)
        save_exploration_metadata(
            db_path=db_path,
            scope_key=scope_key,
            source=source,
            scope=scope_payload,
            newest_id=first_seen_identity,
            oldest_id=last_seen_identity,
            last_explored_page=last_scanned_page,
        )
    _raise_if_cancelled(cancelled)
    prune_stats = prune_storage(
        db_path,
        explored_capacity=explored_capacity,
        unranked_capacity=unranked_capacity,
        ranked_capacity=ranked_capacity,
    )
    if (
        prune_stats.deleted_explored
        or prune_stats.deleted_unranked
        or prune_stats.deleted_ranked
    ):
        _emit(
            messages,
            progress,
            (
                "Pruned storage: "
                f"explored {prune_stats.deleted_explored}; "
                f"unranked {prune_stats.deleted_unranked}; "
                f"ranked {prune_stats.deleted_ranked}."
            ),
        )
    return FetchWorkflowResult(
        source=source,
        db_path=db_path,
        stats=stats,
        prune_stats=prune_stats,
        matched_count=len(matches),
        matches=matches,
        messages=messages,
        request_summaries=[request_summary],
        provider_keys=frozenset(provider_keys),
    )
