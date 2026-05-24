from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class JobOffer(BaseModel):
    """Normalized representation used by every job source."""

    source: str
    source_id: str | None = None
    title: str
    company: str
    url: HttpUrl
    description: str = ""
    published_at: str | None = None
    location: str | None = None
    tags: list[str] = Field(default_factory=list)
    remote: bool | None = None
    salary: str | None = None
    raw_json: dict[str, Any] | None = None
