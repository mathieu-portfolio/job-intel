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
    positive_signals: dict[str, int] = Field(default_factory=dict)
    negative_signals: dict[str, int] = Field(default_factory=dict)
    exclusions: list[str] = Field(default_factory=list)
    screening_threshold: int = 40
    no_signal_score: int = 20
    positive_signal_weight: int = 8
    negative_signal_weight: int = -10
    positive_score_scale: int = 3
    negative_score_scale: int = 4
    strong_negative_threshold: int = -20
    strong_negative_score_cap: int = 10
