from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


Recommendation = Literal["high", "medium", "low", "skip"]


class WeightedTermMatch(BaseModel):
    term: str
    weight: int


class RuleEvaluation(BaseModel):
    score: int
    normalized_score: int = Field(ge=0, le=100)
    matched_positive_terms: list[WeightedTermMatch] = Field(default_factory=list)
    matched_negative_terms: list[WeightedTermMatch] = Field(default_factory=list)
    decision: Recommendation
    reasoning: list[str] = Field(default_factory=list)


class AiJobEvaluation(BaseModel):
    fit_score: int = Field(ge=0, le=100)
    technical_fit_score: int = Field(ge=0, le=100)
    seniority_fit_score: int = Field(ge=0, le=100)
    learning_potential_score: int = Field(ge=0, le=100)
    wording_risk_score: int = Field(ge=0, le=100)
    portfolio_alignment_score: int = Field(ge=0, le=100)
    summary: str
    recommendation: Recommendation
    reasoning: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    suggested_positioning: list[str] | None = None

    @model_validator(mode="after")
    def recommendation_must_match_scores(self) -> "AiJobEvaluation":
        if self.recommendation == "high" and self.fit_score < 70:
            raise ValueError("high recommendation requires fit_score >= 70")
        if self.recommendation == "medium" and self.fit_score < 50:
            raise ValueError("medium recommendation requires fit_score >= 50")
        if self.recommendation == "skip" and self.fit_score > 55:
            raise ValueError("skip recommendation requires fit_score <= 55")
        if self.seniority_fit_score < 35 and self.recommendation in {"high", "medium"}:
            raise ValueError("seniority mismatch cannot have high or medium recommendation")
        return self


class FinalDecision(BaseModel):
    final_score: int = Field(ge=0, le=100)
    recommendation: Recommendation
    rule_component: int = Field(ge=0, le=100)
    ai_component: int | None = Field(default=None, ge=0, le=100)
    penalty_component: int = Field(ge=0, le=100)
    seniority_mismatch_penalty: int = Field(ge=0, le=100)
    reasoning: list[str] = Field(default_factory=list)
