from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class ProfileSignalItem(BaseModel):
    term: str
    weight: float = 1.0


class ProfileSignalCategory(BaseModel):
    weight: float
    items: list[ProfileSignalItem] = Field(default_factory=list)


def _items_from_terms(terms: list[str], *, weight: float = 1.0) -> list[dict[str, float | str]]:
    return [{"term": term, "weight": weight} for term in terms if term.strip()]


def _items_from_weight_map(weights: dict[str, int | float]) -> list[dict[str, float | str]]:
    if not weights:
        return []
    max_weight = max(abs(float(weight)) for weight in weights.values()) or 1.0
    return [
        {"term": term, "weight": abs(float(weight)) / max_weight}
        for term, weight in weights.items()
        if term.strip()
    ]


def _legacy_signal_category_weight(
    weights: dict[str, int | float],
    *,
    fallback: float,
    sign: int,
) -> float:
    if not weights:
        return fallback
    scaled = max(abs(float(weight)) for weight in weights.values()) / 100
    return sign * max(abs(fallback), scaled)


class CandidateProfile(BaseModel):
    name: str | None = None
    signals: dict[str, ProfileSignalCategory] = Field(default_factory=dict)
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
    positive_score_scale: float = 80
    negative_score_scale: float = 80
    strong_negative_threshold: int = -20
    strong_negative_score_cap: int = 10

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_signals(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if data.get("signals"):
            return data

        signals: dict[str, dict[str, object]] = {}
        legacy_positive = data.get("positive_signals") or {}
        legacy_negative = data.get("negative_signals") or {}

        legacy_lists: list[tuple[str, float, list[str]]] = [
            ("interests", 0.25, data.get("interests") or []),
            ("preferred_domains", 0.15, data.get("preferred_domains") or []),
            ("strengths", 0.20, data.get("strengths") or []),
            ("portfolio_projects", 0.15, data.get("portfolio_projects") or []),
            ("location_preferences", 0.15, data.get("location_preferences") or []),
            ("disliked_work", -0.20, data.get("disliked_work") or []),
            ("exclusions", -0.80, data.get("exclusions") or []),
        ]
        for name, category_weight, terms in legacy_lists:
            items = _items_from_terms([str(term) for term in terms])
            if items:
                signals[name] = {"weight": category_weight, "items": items}

        positive_items = _items_from_weight_map(legacy_positive)
        if positive_items:
            signals["positive_signals"] = {
                "weight": _legacy_signal_category_weight(
                    legacy_positive,
                    fallback=float(data.get("positive_signal_weight") or 8) / 100,
                    sign=1,
                ),
                "items": positive_items,
            }

        negative_items = _items_from_weight_map(legacy_negative)
        if negative_items:
            signals["negative_signals"] = {
                "weight": _legacy_signal_category_weight(
                    legacy_negative,
                    fallback=float(data.get("negative_signal_weight") or -10) / 100,
                    sign=-1,
                ),
                "items": negative_items,
            }

        migrated = dict(data)
        migrated["signals"] = signals
        return migrated
