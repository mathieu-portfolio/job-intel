from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.storage.sqlite import (
    DEFAULT_DB_PATH,
    clear_rankings,
    get_review_filter_options,
    list_ranked_offers,
    list_unranked_review_offers,
    update_offer_status,
)
from app.workflows import fetch_offers, rank_offers


UI_DIR = Path(__file__).parent / "ui"
templates = Jinja2Templates(directory=str(UI_DIR / "templates"))
DEFAULT_RECENCY_DAYS = 30


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


def _optional_path(value: str | None) -> Path | None:
    cleaned = (value or "").strip()
    return Path(cleaned) if cleaned else None


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


def create_app(db_path: Path = DEFAULT_DB_PATH) -> FastAPI:
    app = FastAPI(title="Job Intel Review")
    app.state.db_path = db_path
    app.state.workflow_notice = None
    app.mount("/static", StaticFiles(directory=str(UI_DIR / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
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
                    "ranking_mode": ranking_mode or "",
                    "recency": recency_days,
                    "ai_only": ai_only,
                    "sort": sort,
                    "limit": limit,
                },
                "options": get_review_filter_options(request.app.state.db_path),
                "db_path": request.app.state.db_path,
                "workflow_notice": request.app.state.workflow_notice,
                "active_page": "ranked",
            },
        )

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
                "db_path": request.app.state.db_path,
                "active_page": "offers",
            },
        )

    @app.post("/workflows/fetch")
    async def run_fetch(request: Request):
        form = await _form_data(request)
        try:
            preview_limit = _positive_int(form.get("limit"), 20)
            result = fetch_offers(
                source=form.get("source") or "arbeitnow",  # type: ignore[arg-type]
                page=_positive_int(form.get("page"), 1),
                new_offers=_positive_int(form.get("new_offers"), 20),
                max_pages=_positive_int(form.get("max_pages"), 10),
                consecutive_seen_limit=_positive_int(form.get("consecutive_seen_limit"), 100),
                query=form.get("query") or "c++ simulation",
                country=form.get("country") or "fr",
                where=(form.get("where") or "").strip() or None,
                db_path=request.app.state.db_path,
                min_score=_positive_int(form.get("min_score"), 40),
            )
            request.app.state.workflow_notice = _workflow_notice(
                "success",
                "Fetch complete",
                {
                    "Pages scanned": result.stats.pages_scanned,
                    "Fetched": result.stats.fetched,
                    "Explored": result.stats.explored,
                    "Already seen": result.stats.already_seen,
                    "Filtered out": result.stats.filtered_out,
                    "Inserted": result.stats.inserted,
                    "Updated": result.stats.updated,
                    "Errors": result.stats.errors,
                    "Matched": result.matched_count,
                    "Source": result.source,
                    "Preview limit": preview_limit,
                },
                [
                    f"{job.title} at {job.company} ({evaluation.normalized_score}/100)"
                    for job, evaluation in result.matches[:preview_limit]
                ],
            )
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice(
                "error",
                "Fetch failed",
                {"Error": str(error)},
            )
        return RedirectResponse("/", status_code=303)

    @app.post("/workflows/rank")
    async def run_rank(request: Request):
        form = await _form_data(request)
        try:
            weights_path = _optional_path(form.get("weights_path"))
            result = rank_offers(
                profile_path=Path(form.get("profile") or "profiles/default.json"),
                db_path=request.app.state.db_path,
                limit=_positive_int(form.get("limit"), 10),
                only_recent_days=_optional_positive_int(form.get("only_recent_days")),
                min_score=_positive_int(form.get("min_score"), 40),
                weights_path=weights_path,
                ranking_mode=form.get("ranking_mode") or "hybrid",  # type: ignore[arg-type]
                provider=((form.get("provider") or "").strip() or None),  # type: ignore[arg-type]
                model=(form.get("model") or "").strip() or None,
            )
            request.app.state.workflow_notice = _workflow_notice(
                "success",
                "Rank complete",
                {
                    "Selected jobs": result.selected_count,
                    "Prefiltered jobs": result.prefiltered_count,
                    "AI-evaluated jobs": result.ai_evaluation_count,
                    "Skipped jobs": result.skipped_count,
                    "Saved rankings": result.saved_count,
                    "Run": result.run_id or "none",
                },
                result.messages,
            )
        except Exception as error:
            request.app.state.workflow_notice = _workflow_notice(
                "error",
                "Rank failed",
                {"Error": str(error)},
            )
        return RedirectResponse("/", status_code=303)

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
        clear_rankings(request.app.state.db_path)
        return RedirectResponse("/", status_code=303)

    return app
