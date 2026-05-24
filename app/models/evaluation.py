from __future__ import annotations

from pydantic import BaseModel


class RuleEvaluation(BaseModel):
    score: int
    matched_positive_terms: list[str] = []
    matched_negative_terms: list[str] = []
    decision: str
