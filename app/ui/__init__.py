from __future__ import annotations

from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app.storage.connection import DEFAULT_DB_PATH
from app.storage.files import profile_id_from_path
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
from app.ui.state import (
    _cancellation_event,
    _clear_cancellation_event,
    _clear_workflow_progress,
    _consume_workflow_notice,
    _form_data,
    _nonnegative_float,
    _optional_positive_int,
    _positive_int,
    _record_workflow_progress,
    _workflow_notice,
    _workflow_token,
)


UI_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(UI_DIR / "templates"))
DEFAULT_RECENCY_DAYS = 30
CLEAR_SUMMARIES = {
    "rankings": "Deletes AI review rows and legacy ranking rows for the active profile.",
    "offers": "Deletes screened-offer state for the active profile. Shared raw offers remain available to other profiles.",
    "explored": "Deletes provider exploration history for the active profile. Existing screened offers and AI reviews remain.",
    "all": "Deletes explored tracking, screened-offer state, AI reviews, and run metadata for the active profile.",
}



def _active_profile_path(request: Request) -> str:
    profiles = discover_profiles()
    default = profiles[0]["value"] if profiles else "profiles/default.json"
    selected = (request.cookies.get("job_intel_active_profile") or default).strip()
    values = {profile["value"] for profile in profiles}
    return selected if selected in values else default


def _active_profile_context(request: Request) -> dict[str, str]:
    active_value = _active_profile_path(request)
    profiles = discover_profiles()
    active = next((profile for profile in profiles if profile["value"] == active_value), None)
    return {
        "value": active_value,
        "id": profile_id_from_path(active_value),
        "label": active["label"] if active else Path(active_value).stem.title(),
    }


def _common_template_context(request: Request) -> dict[str, object]:
    return {
        "profiles": discover_profiles(),
        "active_profile": _active_profile_context(request),
    }

def create_app(db_path: Path = DEFAULT_DB_PATH) -> FastAPI:
    app = FastAPI(title="Job Intel Review")
    app.state.db_path = db_path
    app.state.workflow_notice = None
    app.state.workflow_cancellations = {}
    app.state.workflow_progress = {}
    app.state.workflow_progress_lock = Lock()
    app.mount("/static", StaticFiles(directory=str(UI_DIR / "static")), name="static")


    @app.post("/settings/profile")
    async def set_active_profile(request: Request):
        form = await _form_data(request)
        selected = (form.get("active_profile") or "").strip()
        valid_values = {profile["value"] for profile in discover_profiles()}
        if selected not in valid_values:
            raise HTTPException(status_code=400, detail="Unknown profile.")
        redirect_to = (form.get("redirect_to") or request.headers.get("referer") or "/").strip()
        if not redirect_to.startswith("/") or redirect_to.startswith("//"):
            redirect_to = "/"
        response = RedirectResponse(redirect_to, status_code=303)
        response.set_cookie("job_intel_active_profile", selected, max_age=60 * 60 * 24 * 365, samesite="lax")
        return response

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
        active_profile = _active_profile_path(request)
        offers = list_ranked_offers(
            db_path=request.app.state.db_path,
            recommendation=recommendation or None,
            status=status or None,
            source=source or None,
            location=location or None,
            ranking_mode=ranking_mode or None,
            profile_path=active_profile,
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
                    "ranking_mode": ranking_mode or "",
                    "recency": recency_days,
                    "ai_only": ai_only,
                    "sort": sort,
                    "limit": limit,
                },
                "options": get_review_filter_options(request.app.state.db_path),
                "scoring_presets": list_scoring_presets(request.app.state.db_path, enabled_only=True),
                **_common_template_context(request),
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
        active_profile = _active_profile_path(request)
        offers = list_unranked_review_offers(
            db_path=request.app.state.db_path,
            search=q or None,
            source=source or None,
            profile_id=profile_id_from_path(active_profile),
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
                **_common_template_context(request),
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
        active_profile = _active_profile_path(request)
        offers = list_screened_offers(
            db_path=request.app.state.db_path,
            preset_id=preset,
            profile_id=profile_id_from_path(active_profile),
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
                **_common_template_context(request),
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
                "storage_counts": get_storage_counts(
                    request.app.state.db_path,
                    profile_id=profile_id_from_path(_active_profile_path(request)),
                ),
                "clear_summaries": CLEAR_SUMMARIES,
                **_common_template_context(request),
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
                profile_path=Path(_active_profile_path(request)),
                db_path=request.app.state.db_path,
                limit=_positive_int(form.get("limit"), 10),
                only_recent_days=_optional_positive_int(form.get("only_recent_days")),
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
        clear_data(
            db_path=request.app.state.db_path,
            scope="rankings",
            profile_id=profile_id_from_path(_active_profile_path(request)),
        )
        return RedirectResponse("/", status_code=303)

    @app.post("/storage/clear")
    async def clear_storage_action(request: Request):
        form = await _form_data(request)
        scope = (form.get("scope") or "").strip()
        try:
            result = clear_data(
                db_path=request.app.state.db_path,
                scope=scope,
                profile_id=profile_id_from_path(_active_profile_path(request)),
            )
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
