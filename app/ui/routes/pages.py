from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from app.storage.files import profile_id_from_path
from app.storage.maintenance import (
    DEFAULT_EXPLORED_CAPACITY,
    DEFAULT_RANKED_CAPACITY,
    DEFAULT_UNRANKED_CAPACITY,
    get_storage_counts,
)
from app.storage.offers import list_offer_locations
from app.storage.reviews import get_review_filter_options, list_ranked_offers, list_unranked_review_offers
from app.storage.scoring import get_scoring_preset, list_scoring_presets, list_screened_offers
from app.ui.context import _active_profile_path, _common_template_context
from app.ui.review_display import _normalize_review_offers
from app.ui.shared import CLEAR_SUMMARIES, DEFAULT_RECENCY_DAYS, templates
from app.ui.state import _consume_workflow_notice, _positive_int
from app.ui_options import ADZUNA_MARKETS


def register_page_routes(app: FastAPI) -> None:
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
        preset: str = "balanced",
        sort: str = "score_desc",
        limit: int = 100,
    ) -> HTMLResponse:
        recency_days = _positive_int(recency, DEFAULT_RECENCY_DAYS)
        active_profile = _active_profile_path(request)
        scoring_presets = list_scoring_presets(request.app.state.db_path, enabled_only=True)
        selected_preset = get_scoring_preset(preset, db_path=request.app.state.db_path)
        offers = list_ranked_offers(
            db_path=request.app.state.db_path,
            recommendation=recommendation or None,
            status=status or None,
            source=source or None,
            location=location or None,
            ranking_mode=ranking_mode or None,
            profile_path=active_profile,
            preset=selected_preset,
            only_recent_days=recency_days,
            ai_only=ai_only,
            sort=sort,
            limit=limit,
        )
        _normalize_review_offers(offers)
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
                    "preset": selected_preset.id,
                    "sort": sort,
                    "limit": limit,
                },
                "options": get_review_filter_options(request.app.state.db_path),
                "scoring_presets": scoring_presets,
                **_common_template_context(request),
                "location_suggestions": list_offer_locations(request.app.state.db_path),
                "db_path": request.app.state.db_path,
                "workflow_notice": _consume_workflow_notice(request),
                "workflow_count_label": f"{len(offers)} AI reviewed offers",
                "active_page": "ai_reviewed",
            },
        )

    @app.get("/explore", response_class=HTMLResponse)
    @app.get("/offers", response_class=HTMLResponse)
    def fetched_offers(request: Request, q: str | None = None, source: str | None = None, limit: int = 100) -> HTMLResponse:
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
                "filters": {"q": q or "", "source": source or "", "limit": limit},
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
                "workflow_count_label": f"{len(offers)} offers",
                "active_page": "explore",
            },
        )

    @app.get("/screened", response_class=HTMLResponse)
    def screened_offers(
        request: Request,
        q: str | None = None,
        source: str | None = None,
        preset: str = "balanced",
        show_all_presets: bool = False,
        sort: str = "score_desc",
        limit: int = 100,
    ) -> HTMLResponse:
        active_profile = _active_profile_path(request)
        offers = list_screened_offers(
            db_path=request.app.state.db_path,
            preset_id=preset,
            profile_id=profile_id_from_path(active_profile),
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
                "workflow_count_label": f"{len(offers)} screened",
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
                "storage_counts": get_storage_counts(request.app.state.db_path, profile_id=profile_id_from_path(_active_profile_path(request))),
                "clear_summaries": CLEAR_SUMMARIES,
                **_common_template_context(request),
                "workflow_count_label": "Maintenance",
                "active_page": "maintenance",
            },
        )
