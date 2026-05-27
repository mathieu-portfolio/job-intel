from __future__ import annotations

from threading import Event, Lock
from pathlib import Path
from urllib.parse import parse_qs
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app.storage.connection import DEFAULT_DB_PATH
from app.storage.maintenance import (
    DEFAULT_EXPLORED_CAPACITY,
    DEFAULT_RANKED_CAPACITY,
    DEFAULT_UNRANKED_CAPACITY,
    clear_data,
    get_storage_counts,
    prune_storage,
)
from app.storage.offers import list_offer_locations, update_offer_status
from app.storage.reviews import (
    get_review_filter_options,
    list_ranked_offers,
    list_unranked_review_offers,
)
from app.storage.scoring import list_scoring_presets, list_screened_offers
from app.ui_options import ADZUNA_MARKETS, discover_profiles
from app.workflows import WorkflowCancelled, fetch_offers, rank_offers


UI_DIR = Path(__file__).parent / "ui"
templates = Jinja2Templates(directory=str(UI_DIR / "templates"))
DEFAULT_RECENCY_DAYS = 30
CLEAR_SUMMARIES = {
    "rankings": "Deletes AI review rows and legacy ranking rows.",
    "offers": "Deletes screened offers. Dependent AI review rows are removed by SQLite foreign keys.",
    "explored": "Deletes provider exploration history. Existing offers and AI reviews remain.",
    "all": "Deletes explored tracking, screened offers, AI reviews, and run metadata.",
}
def _positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value or "")
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _optional_positive_int(value: str | None) -> int | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    parsed = _positive_int(cleaned, 0)
    return parsed or None


def _nonnegative_float(value: str | None, default: float) -> float:
    try:
        parsed = float(value or "")
    except ValueError:
        return default
    return parsed if parsed >= 0 else default


def _workflow_notice(kind: str, title: str, summary: dict[str, object], messages: list[str] | None = None) -> dict[str, object]:
    return {
        "kind": kind,
        "title": title,
        "summary": summary,
        "messages": messages or [],
    }


async def _form_data(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items() if values}


def _consume_workflow_notice(request: Request) -> dict[str, object] | None:
    notice = request.app.state.workflow_notice
    request.app.state.workflow_notice = None
    return notice


def _workflow_token(request: Request) -> str:
    token = request.headers.get("x-workflow-token", "").strip()
    return token or uuid4().hex


def _cancellation_event(request: Request, token: str) -> Event:
    event = Event()
    request.app.state.workflow_cancellations[token] = event
    return event


def _clear_cancellation_event(request: Request, token: str) -> None:
    request.app.state.workflow_cancellations.pop(token, None)


def _record_workflow_progress(request: Request, token: str, message: str) -> None:
    progress = {"message": message}
    with request.app.state.workflow_progress_lock:
        previous = dict(request.app.state.workflow_progress.get(token, {}))
    total = previous.get("total")
    current = previous.get("current")

    if message.startswith("Evaluating "):
        prefix = message.removeprefix("Evaluating ").split(":", 1)[0]
        try:
            current_text, total_text = prefix.split("/", 1)
            current = int(current_text)
            total = int(total_text)
            progress["current"] = current
            progress["total"] = total
            progress["remaining"] = max(total - current + 1, 0)
        except ValueError:
            pass
    elif message.startswith("Completed ") and "/" in message and " AI evaluations" in message:
        prefix = message.removeprefix("Completed ").split(" AI evaluations", 1)[0]
        try:
            current_text, total_text = prefix.split("/", 1)
            current = int(current_text)
            total = int(total_text)
            progress["current"] = current
            progress["total"] = total
            progress["remaining"] = max(total - current, 0)
        except ValueError:
            pass
    elif message.startswith("Model response parsed") and total is not None and current is not None:
        progress["current"] = current
        progress["total"] = total
        progress["remaining"] = max(total - current, 0)
    elif message.startswith("Saved ") and " rankings" in message and total is not None:
        progress["current"] = total
        progress["total"] = total
        progress["remaining"] = 0
    elif "AI-evaluated " in message:
        try:
            total = int(message.split("AI-evaluated ", 1)[1].split(";", 1)[0])
            progress["current"] = 0
            progress["total"] = total
            progress["remaining"] = total
        except ValueError:
            pass
    elif message.startswith("Processed ") and "/" in message and " newly explored offers" in message:
        prefix = message.removeprefix("Processed ").split(" newly explored offers", 1)[0]
        try:
            current_text, total_text = prefix.split("/", 1)
            current = int(current_text)
            total = int(total_text)
            progress["current"] = current
            progress["total"] = total
            progress["remaining"] = max(total - current, 0)
        except ValueError:
            pass

    with request.app.state.workflow_progress_lock:
        merged = dict(previous)
        merged.update(progress)
        request.app.state.workflow_progress[token] = merged


def _clear_workflow_progress(request: Request, token: str) -> None:
    with request.app.state.workflow_progress_lock:
        request.app.state.workflow_progress.pop(token, None)


def create_app(db_path: Path = DEFAULT_DB_PATH) -> FastAPI:
    app = FastAPI(title="Job Intel Review")
    app.state.db_path = db_path
    app.state.workflow_notice = None
    app.state.workflow_cancellations = {}
    app.state.workflow_progress = {}
    app.state.workflow_progress_lock = Lock()
    app.mount("/static", StaticFiles(directory=str(UI_DIR / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    @app.get("/ai-reviewed", response_class=HTMLResponse)
    def index(
        request: Request,
        recommendation: str | None = None,
        status: str | None = None,
        source: str | None = None,
        location: str | None = None,
        ranking_mode: str | None = None,
        recency: str | None = None,
        ai_only: bool = False,
        sort: str = "score_desc",
        limit: int = 100,
    ) -> HTMLResponse:
        recency_days = _positive_int(recency, DEFAULT_RECENCY_DAYS)
        offers = list_ranked_offers(
            db_path=request.app.state.db_path,
            recommendation=recommendation or None,
            status=status or None,
            source=source or None,
            location=location or None,
            ranking_mode=ranking_mode or None,
            only_recent_days=recency_days,
            ai_only=ai_only,
            sort=sort,
            limit=limit,
        )
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "offers": offers,
                "filters": {
                    "recommendation": recommendation or "",
                    "status": status or "",
                    "source": source or "",
                    "location": location or "",
                "recency": recency_days,
                    "ai_only": ai_only,
                    "sort": sort,
                    "limit": limit,
                },
                "options": get_review_filter_options(request.app.state.db_path),
                "scoring_presets": list_scoring_presets(request.app.state.db_path, enabled_only=True),
                "profiles": discover_profiles(),
                "location_suggestions": list_offer_locations(request.app.state.db_path),
                "db_path": request.app.state.db_path,
                "workflow_notice": _consume_workflow_notice(request),
                "active_page": "ai_reviewed",
            },
        )

    @app.get("/explore", response_class=HTMLResponse)
    @app.get("/offers", response_class=HTMLResponse)
    def fetched_offers(
        request: Request,
        q: str | None = None,
        source: str | None = None,
        limit: int = 100,
    ) -> HTMLResponse:
        offers = list_unranked_review_offers(
            db_path=request.app.state.db_path,
            search=q or None,
            source=source or None,
            limit=limit,
        )
        return templates.TemplateResponse(
            request,
            "offers.html",
            {
                "offers": offers,
                "filters": {
                    "q": q or "",
                    "source": source or "",
                    "limit": limit,
                },
                "options": get_review_filter_options(request.app.state.db_path),
                "location_suggestions": list_offer_locations(request.app.state.db_path),
                "adzuna_markets": ADZUNA_MARKETS,
                "profiles": discover_profiles(),
                "db_path": request.app.state.db_path,
                "workflow_notice": _consume_workflow_notice(request),
                "storage_capacities": {
                    "explored": DEFAULT_EXPLORED_CAPACITY,
                    "unranked": DEFAULT_UNRANKED_CAPACITY,
                    "ranked": DEFAULT_RANKED_CAPACITY,
                },
                "show_fetch_workflow": True,
                "page_title": "Explore",
                "empty_message": "No screened offers match these filters.",
                "listing_path": "/explore",
                "active_page": "explore",
            },
        )

    @app.get("/screened", response_class=HTMLResponse)
    def screened_offers(
        request: Request,
        q: str | None = None,
        source: str | None = None,
        preset: str = "balanced",
        threshold: int = 40,
        show_all_presets: bool = False,
        sort: str = "score_desc",
        limit: int = 100,
    ) -> HTMLResponse:
        offers = list_screened_offers(
            db_path=request.app.state.db_path,
            preset_id=preset,
            threshold=threshold,
            show_all_matching_presets=show_all_presets,
            search=q or None,
            source=source or None,
            sort=sort,
            limit=limit,
        )
        scoring_presets = list_scoring_presets(request.app.state.db_path, enabled_only=True)
        return templates.TemplateResponse(
            request,
            "offers.html",
            {
                "offers": offers,
                "filters": {
                    "q": q or "",
                    "source": source or "",
                    "preset": preset,
                    "threshold": threshold,
                    "show_all_presets": show_all_presets,
                    "sort": sort,
                    "limit": limit,
                },
                "options": get_review_filter_options(request.app.state.db_path),
                "scoring_presets": scoring_presets,
                "location_suggestions": list_offer_locations(request.app.state.db_path),
                "adzuna_markets": ADZUNA_MARKETS,
                "profiles": discover_profiles(),
                "db_path": request.app.state.db_path,
                "workflow_notice": _consume_workflow_notice(request),
                "storage_capacities": {
                    "explored": DEFAULT_EXPLORED_CAPACITY,
                    "unranked": DEFAULT_UNRANKED_CAPACITY,
                    "ranked": DEFAULT_RANKED_CAPACITY,
                },
                "show_fetch_workflow": False,
                "show_screened_filters": True,
                "page_title": "Screened",
                "empty_message": "No screened offers match these filters.",
                "listing_path": "/screened",
                "active_page": "screened",
            },
        )

    @app.get("/maintenance", response_class=HTMLResponse)
    def maintenance(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "maintenance.html",
            {
                "db_path": request.app.state.db_path,
                "workflow_notice": _consume_workflow_notice(request),
                "storage_counts": get_storage_counts(request.app.state.db_path),
                "clear_summaries": CLEAR_SUMMARIES,
                "active_page": "maintenance",
            },
        )

    @app.post("/workflows/fetch")
    async def run_fetch(request: Request):
        form = await _form_data(request)
        token = _workflow_token(request)
        cancellation = _cancellation_event(request, token)
        progress = lambda message: _record_workflow_progress(request, token, message)
        try:
            preview_limit = _positive_int(form.get("limit"), 20)
            target_new_offers = _positive_int(form.get("new_offers"), 20)
            progress(f"Processed 0/{target_new_offers} newly explored offers.")
            source = form.get("source") or "arbeitnow"
            country = (form.get("country") or "").strip()
            if source == "adzuna" and not country:
                raise ValueError("Market is required when fetching from Adzuna.")
            result = await run_in_threadpool(
                fetch_offers,
                source=source,  # type: ignore[arg-type]
                page=_positive_int(form.get("page"), 1),
                new_offers=target_new_offers,
                max_pages=_positive_int(form.get("max_pages"), 10),
                max_seen_pages=_positive_int(form.get("max_seen_pages"), 50),
                query=(form.get("query") or "").strip(),
                country=country or "fr",
                where=(form.get("location") or "").strip() or None,
                profile_path=Path(form.get("profile")) if form.get("profile") else None,
                db_path=request.app.state.db_path,
                min_score=_positive_int(form.get("min_score"), 40),
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
                [
                    f"{job.title} at {job.company} ({evaluation.normalized_score}/100)"
                    for job, evaluation in result.matches[:preview_limit]
                ],
            )
        except WorkflowCancelled:
            request.app.state.workflow_notice = _workflow_notice(
                "error",
                "Fetch cancelled",
                {"Status": "Cancelled by user"},
            )
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice(
                "error",
                "Fetch failed",
                {"Error": str(error)},
            )
        finally:
            _clear_cancellation_event(request, token)
            _clear_workflow_progress(request, token)
        return RedirectResponse("/explore", status_code=303)

    @app.post("/workflows/rank")
    async def run_rank(request: Request):
        form = await _form_data(request)
        token = _workflow_token(request)
        cancellation = _cancellation_event(request, token)
        progress = lambda message: _record_workflow_progress(request, token, message)
        try:
            result = await run_in_threadpool(
                rank_offers,
                profile_path=Path(form.get("profile")) if form.get("profile") else None,
                db_path=request.app.state.db_path,
                limit=_positive_int(form.get("limit"), 10),
                only_recent_days=_optional_positive_int(form.get("only_recent_days")),
                min_score=_positive_int(form.get("min_score"), 40),
                ranking_mode="ai",
                provider=((form.get("provider") or "").strip() or None),  # type: ignore[arg-type]
                model=(form.get("model") or "").strip() or None,
                preset_id=(form.get("preset") or "balanced").strip() or "balanced",
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
            request.app.state.workflow_notice = _workflow_notice(
                "error",
                "Rank cancelled",
                {"Status": "Cancelled by user"},
            )
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice(
                "error",
                "Rank failed",
                {"Error": str(error)},
            )
        finally:
            _clear_cancellation_event(request, token)
            _clear_workflow_progress(request, token)
        return RedirectResponse("/", status_code=303)

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

    @app.post("/offers/{offer_id}/status/{status}", response_model=None)
    def set_offer_status(request: Request, offer_id: int, status: str):
        try:
            update_offer_status(
                db_path=request.app.state.db_path,
                offer_id=offer_id,
                status=status,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if request.headers.get("HX-Request") != "true":
            return RedirectResponse(
                request.headers.get("referer") or "/",
                status_code=303,
            )
        return templates.TemplateResponse(
            request,
            "_status_controls.html",
            {"offer": {"offer_id": offer_id, "review_status": status}},
        )

    @app.post("/rankings/clear")
    def clear_all_rankings(request: Request):
        clear_data(db_path=request.app.state.db_path, scope="rankings")
        return RedirectResponse("/", status_code=303)

    @app.post("/storage/clear")
    async def clear_storage_action(request: Request):
        form = await _form_data(request)
        scope = (form.get("scope") or "").strip()
        try:
            result = clear_data(db_path=request.app.state.db_path, scope=scope)
            request.app.state.workflow_notice = _workflow_notice(
                "success",
                "Clear complete",
                {
                    "Scope": result.scope,
                    "Deleted explored": result.explored,
                    "Deleted offers": result.offers,
                    "Deleted AI reviews": result.rankings,
                    "Deleted ranking runs": result.ranking_runs,
                },
            )
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice(
                "error",
                "Clear failed",
                {"Error": str(error)},
            )
        redirect_to = (form.get("redirect_to") or request.headers.get("referer") or "/").strip()
        if not redirect_to.startswith("/") or redirect_to.startswith("//"):
            redirect_to = "/"
        return RedirectResponse(redirect_to, status_code=303)

    @app.post("/storage/prune")
    async def prune_storage_action(request: Request):
        form = await _form_data(request)
        try:
            result = prune_storage(
                request.app.state.db_path,
                explored_capacity=_positive_int(form.get("explored_capacity"), DEFAULT_EXPLORED_CAPACITY),
                unranked_capacity=_positive_int(form.get("unranked_capacity"), DEFAULT_UNRANKED_CAPACITY),
                ranked_capacity=_positive_int(form.get("ranked_capacity"), DEFAULT_RANKED_CAPACITY),
            )
            request.app.state.workflow_notice = _workflow_notice(
                "success",
                "Cleanup complete",
                {
                    "Deleted explored": result.deleted_explored,
                    "Deleted unranked": result.deleted_unranked,
                    "Deleted ranked": result.deleted_ranked,
                    "Explored count": result.after.explored,
                    "Unranked count": result.after.unranked,
                    "Ranked count": result.after.ranked,
                },
            )
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice(
                "error",
                "Cleanup failed",
                {"Error": str(error)},
            )
        return RedirectResponse("/", status_code=303)

    return app
