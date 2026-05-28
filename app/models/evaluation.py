from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Recommendation = Literal["high", "medium", "low", "skip"]
SeniorityLevel = Literal["internship", "junior", "mid", "senior", "lead", "unknown"]


def recommendation_from_score(score: int) -> Recommendation:
    if score >= 75:
        return "high"
    if score >= 60:
        return "medium"
    if score >= 40:
        return "low"
    return "skip"


class WeightedTermMatch(BaseModel):
    term: str
    weight: float
    category: str | None = None
    matched_alias: str | None = None
    language: str | None = None
    contribution: float | None = None


class SeniorityEvaluation(BaseModel):
    target_seniority: SeniorityLevel = "unknown"
    offer_seniority: SeniorityLevel = "unknown"
    score: int = Field(ge=0, le=100)
    confidence: int = Field(ge=0, le=100)
    reasoning: list[str] = Field(default_factory=list)


class CategoryScore(BaseModel):
    matched_weight: float = 0.0
    total_weight: float = 0.0
    ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    mode: Literal["cumulative", "exclusive"] = "cumulative"


class RuleEvaluation(BaseModel):
    score: int
    raw_score: float = 0.0
    normalized_score: int = Field(ge=0, le=100)
    matched_positive_terms: list[WeightedTermMatch] = Field(default_factory=list)
    matched_negative_terms: list[WeightedTermMatch] = Field(default_factory=list)
    decision: Recommendation
    reasoning: list[str] = Field(default_factory=list)
    seniority: SeniorityEvaluation = Field(default_factory=lambda: SeniorityEvaluation(score=70, confidence=0))
    category_scores: dict[str, CategoryScore] = Field(default_factory=dict)


class AiJobEvaluation(BaseModel):
    fit_score: int = Field(ge=0, le=100)
    technical_fit_score: int = Field(ge=0, le=100)
    domain_fit_score: int = Field(ge=0, le=100)
    role_interest_score: int = Field(ge=0, le=100)
    learning_potential_score: int = Field(ge=0, le=100)
    portfolio_alignment_score: int = Field(ge=0, le=100)
    summary: str
    recommendation: Recommendation
    reasoning: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    suggested_positioning: list[str] | None = None


class FinalDecision(BaseModel):
    final_score: int = Field(ge=0, le=100)
    recommendation: Recommendation
    rule_component: int = Field(ge=0, le=100)
    ai_component: int | None = Field(default=None, ge=0, le=100)
    base_component: int | None = Field(default=None, ge=0, le=100)
    seniority_component: int = Field(ge=0, le=100)
    penalty_component: int = Field(ge=0, le=100)
    seniority_mismatch_penalty: int = Field(ge=0, le=100)
    policy_adjustments: list[str] = Field(default_factory=list)
    reasoning: list[str] = Field(default_factory=list)
