from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.storage.maintenance import DEFAULT_EXPLORED_CAPACITY, DEFAULT_RANKED_CAPACITY, DEFAULT_UNRANKED_CAPACITY
from app.ui.context import _active_profile_path
from app.ui.state import (
    _cancellation_event,
    _clear_cancellation_event,
    _clear_workflow_progress,
    _form_data,
    _nonnegative_float,
    _optional_positive_int,
    _positive_int,
    _record_workflow_progress,
    _workflow_notice,
    _workflow_token,
)
from app.workflows import WorkflowCancelled, fetch_offers, rank_offers


def register_workflow_routes(app: FastAPI) -> None:
    @app.post("/workflows/fetch")
    async def run_fetch(request: Request):
        form = await _form_data(request)
        token = _workflow_token(request)
        cancellation = _cancellation_event(request, token)
        progress = lambda message: _record_workflow_progress(request, token, message)
        try:
            preview_limit = _positive_int(form.get("limit"), 20)
            target_unexplored_offers = _positive_int(form.get("new_offers"), 20)
            progress(f"Processed 0/{target_unexplored_offers} new/unexplored offers.")
            source = form.get("source") or "arbeitnow"
            country = (form.get("country") or "").strip()
            if source == "adzuna" and not country:
                raise ValueError("Market is required when fetching from Adzuna.")
            result = await run_in_threadpool(
                fetch_offers,
                source=source,  # type: ignore[arg-type]
                page=_positive_int(form.get("page"), 1),
                new_offers=target_unexplored_offers,
                max_pages=_positive_int(form.get("max_pages"), 10),
                max_seen_pages=_positive_int(form.get("max_seen_pages"), 50),
                query=(form.get("query") or "").strip(),
                country=country or "fr",
                where=(form.get("location") or "").strip() or None,
                profile_path=Path(_active_profile_path(request)),
                db_path=request.app.state.db_path,
                min_score=None,
                explored_capacity=_positive_int(form.get("explored_capacity"), DEFAULT_EXPLORED_CAPACITY),
                unranked_capacity=_positive_int(form.get("unranked_capacity"), DEFAULT_UNRANKED_CAPACITY),
                ranked_capacity=_positive_int(form.get("ranked_capacity"), DEFAULT_RANKED_CAPACITY),
                exploration_mode=(form.get("exploration_mode") or "safe"),  # type: ignore[arg-type]
                use_profile_queries=form.get("use_profile_queries") == "true",
                fetch_concurrency=_positive_int(form.get("fetch_concurrency"), 1),
                provider_retry_attempts=_positive_int(form.get("provider_retry_attempts"), 1),
                provider_retry_backoff=_nonnegative_float(form.get("provider_retry_backoff"), 0.0),
                progress=progress,
                cancelled=cancellation.is_set,
            )
            request.app.state.workflow_notice = _workflow_notice(
                "success",
                "Fetch complete",
                {
                    "Pages scanned": result.stats.pages_scanned,
                    "Provider rows fetched": result.stats.fetched,
                    "Newly explored": result.stats.newly_explored,
                    "Already seen": result.stats.already_seen,
                    "Screened": result.stats.inserted + result.stats.updated,
                    "Screened out": result.stats.filtered_out,
                    "Inserted": result.stats.inserted,
                    "Updated": result.stats.updated,
                    "Errors": result.stats.errors,
                    "Pruned explored": result.prune_stats.deleted_explored,
                    "Pruned unranked": result.prune_stats.deleted_unranked,
                    "Pruned ranked": result.prune_stats.deleted_ranked,
                    "Matched": result.matched_count,
                    "Source": result.source,
                    "Preview limit": preview_limit,
                },
                [f"{job.title} at {job.company} ({evaluation.normalized_score}/100)" for job, evaluation in result.matches[:preview_limit]],
            )
        except WorkflowCancelled:
            request.app.state.workflow_notice = _workflow_notice("error", "Fetch cancelled", {"Status": "Cancelled by user"})
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice("error", "Fetch failed", {"Error": str(error)})
        finally:
            _clear_cancellation_event(request, token)
            _clear_workflow_progress(request, token)
        return RedirectResponse("/offers?view=ready", status_code=303)

    @app.post("/workflows/rank")
    async def run_rank(request: Request):
        form = await _form_data(request)
        token = _workflow_token(request)
        cancellation = _cancellation_event(request, token)
        progress = lambda message: _record_workflow_progress(request, token, message)
        try:
            result = await run_in_threadpool(
                rank_offers,
                profile_path=Path(_active_profile_path(request)),
                db_path=request.app.state.db_path,
                limit=_positive_int(form.get("limit"), 10),
                only_recent_days=_optional_positive_int(form.get("only_recent_days")),
                ranking_mode="ai",
                provider=((form.get("provider") or "").strip() or None),  # type: ignore[arg-type]
                model=(form.get("model") or "").strip() or None,
                ai_concurrency=_positive_int(form.get("ai_concurrency"), 1),
                ai_retry_attempts=_positive_int(form.get("ai_retry_attempts"), 1),
                ai_retry_backoff=_nonnegative_float(form.get("ai_retry_backoff"), 0.0),
                ai_abort_on_error=form.get("ai_abort_on_error") == "true",
                progress=progress,
                cancelled=cancellation.is_set,
            )
            request.app.state.workflow_notice = _workflow_notice(
                "success",
                "Rank complete",
                {
                    "Selected jobs": result.selected_count,
                    "Reviewed": result.saved_count,
                    "AI-evaluated jobs": result.ai_evaluation_count,
                    "Skipped jobs": result.skipped_count,
                    "Saved AI reviews": result.saved_count,
                    "Run": result.run_id or "none",
                },
                result.messages,
            )
        except WorkflowCancelled:
            request.app.state.workflow_notice = _workflow_notice("error", "Rank cancelled", {"Status": "Cancelled by user"})
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice("error", "Rank failed", {"Error": str(error)})
        finally:
            _clear_cancellation_event(request, token)
            _clear_workflow_progress(request, token)
        return RedirectResponse("/ai-reviewed", status_code=303)

    @app.get("/workflows/progress/{token}")
    async def workflow_progress(request: Request, token: str):
        with request.app.state.workflow_progress_lock:
            progress = dict(request.app.state.workflow_progress.get(token, {}))
        return progress

    @app.post("/workflows/cancel")
    async def cancel_workflow(request: Request):
        form = await _form_data(request)
        token = (form.get("token") or request.headers.get("x-workflow-token") or "").strip()
        event = request.app.state.workflow_cancellations.get(token)
        if event is not None:
            event.set()
        return {"cancelled": event is not None}
