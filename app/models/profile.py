from __future__ import annotations

from pydantic import BaseModel


class CandidateProfile(BaseModel):
    interests: list[str] = []
    preferred_domains: list[str] = []
    disliked_work: list[str] = []
    strengths: list[str] = []
    location_preferences: list[str] = []
    target_seniority: str | None = None
