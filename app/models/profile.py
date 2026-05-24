from __future__ import annotations

from pydantic import BaseModel, Field


class CandidateProfile(BaseModel):
    name: str | None = None
    interests: list[str] = Field(default_factory=list)
    preferred_domains: list[str] = Field(default_factory=list)
    disliked_work: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    portfolio_projects: list[str] = Field(default_factory=list)
    location_preferences: list[str] = Field(default_factory=list)
    target_seniority: str | None = None
