from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

UI_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(UI_DIR / "templates"))

DEFAULT_RECENCY_DAYS = 30
CLEAR_SUMMARIES = {
    "rankings": "Deletes AI review rows and legacy ranking rows for the active profile.",
    "offers": "Deletes screened-offer state for the active profile. Shared raw offers remain available to other profiles.",
    "explored": "Deletes provider exploration history for the active profile. Existing screened offers and AI reviews remain.",
    "all": "Deletes explored tracking, screened-offer state, AI reviews, and run metadata for the active profile.",
}
