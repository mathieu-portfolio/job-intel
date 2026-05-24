from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RuleEvaluation(BaseModel):
    score: int
    matched_positive_terms: list[str] = Field(default_factory=list)
    matched_negative_terms: list[str] = Field(default_factory=list)
    decision: str


class AiJobEvaluation(BaseModel):
    fit_score: int = Field(ge=0, le=100)
    technical_fit_score: int = Field(ge=0, le=100)
    junior_accessibility_score: int = Field(ge=0, le=100)
    learning_potential_score: int = Field(ge=0, le=100)
    wording_risk_score: int = Field(ge=0, le=100)
    portfolio_alignment_score: int = Field(ge=0, le=100)
    recommendation: Literal["strong_apply", "apply", "consider", "skip"]
    reasoning: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    suggested_positioning: list[str] = Field(default_factory=list)
