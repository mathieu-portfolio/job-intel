from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.storage.sqlite import (
    DEFAULT_DB_PATH,
    get_review_filter_options,
    list_ranked_offers,
    update_offer_status,
)


UI_DIR = Path(__file__).parent / "ui"
templates = Jinja2Templates(directory=str(UI_DIR / "templates"))


def create_app(db_path: Path = DEFAULT_DB_PATH) -> FastAPI:
    app = FastAPI(title="Job Intel Review")
    app.state.db_path = db_path
    app.mount("/static", StaticFiles(directory=str(UI_DIR / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        recommendation: str | None = None,
        status: str | None = None,
        source: str | None = None,
        ranking_mode: str | None = None,
        recency: int | None = None,
        sort: str = "score_desc",
        limit: int = 100,
    ) -> HTMLResponse:
        offers = list_ranked_offers(
            db_path=request.app.state.db_path,
            recommendation=recommendation or None,
            status=status or None,
            source=source or None,
            ranking_mode=ranking_mode or None,
            only_recent_days=recency,
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
                    "ranking_mode": ranking_mode or "",
                    "recency": recency or "",
                    "sort": sort,
                    "limit": limit,
                },
                "options": get_review_filter_options(request.app.state.db_path),
                "db_path": request.app.state.db_path,
            },
        )

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

    return app
