from __future__ import annotations

from pydantic import BaseModel, HttpUrl


class JobOffer(BaseModel):
    """Normalized representation used by every job source."""

    source: str
    title: str
    company: str
    url: HttpUrl
    description: str = ""
    location: str | None = None
    tags: list[str] = []
    remote: bool | None = None
    salary: str | None = None
