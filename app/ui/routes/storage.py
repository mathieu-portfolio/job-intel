from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.storage.files import profile_id_from_path
from app.storage.maintenance import DEFAULT_EXPLORED_CAPACITY, DEFAULT_RANKED_CAPACITY, DEFAULT_UNRANKED_CAPACITY, clear_data, prune_storage
from app.storage.offers import update_offer_status
from app.ui.context import _active_profile_path
from app.ui.shared import templates
from app.ui.state import _form_data, _positive_int, _workflow_notice


def _safe_redirect(value: str | None) -> str:
    redirect_to = (value or "/").strip()
    if not redirect_to.startswith("/") or redirect_to.startswith("//"):
        return "/"
    return redirect_to


def register_storage_routes(app: FastAPI) -> None:
    @app.post("/offers/{offer_id}/status/{status}", response_model=None)
    def set_offer_status(request: Request, offer_id: int, status: str):
        try:
            update_offer_status(db_path=request.app.state.db_path, offer_id=offer_id, status=status)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if request.headers.get("HX-Request") != "true":
            return RedirectResponse(request.headers.get("referer") or "/", status_code=303)
        return templates.TemplateResponse(request, "_status_controls.html", {"offer": {"offer_id": offer_id, "review_status": status}})

    @app.post("/rankings/clear")
    def clear_all_rankings(request: Request):
        clear_data(db_path=request.app.state.db_path, scope="rankings", profile_id=profile_id_from_path(_active_profile_path(request)))
        return RedirectResponse("/", status_code=303)

    @app.post("/storage/clear")
    async def clear_storage_action(request: Request):
        form = await _form_data(request)
        scope = (form.get("scope") or "").strip()
        try:
            result = clear_data(db_path=request.app.state.db_path, scope=scope, profile_id=profile_id_from_path(_active_profile_path(request)))
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
            request.app.state.workflow_notice = _workflow_notice("error", "Clear failed", {"Error": str(error)})
        return RedirectResponse(_safe_redirect(form.get("redirect_to") or request.headers.get("referer")), status_code=303)

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
            request.app.state.workflow_notice = _workflow_notice("error", "Cleanup failed", {"Error": str(error)})
        return RedirectResponse("/", status_code=303)
