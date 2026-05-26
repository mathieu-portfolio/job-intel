from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class ProfileSignalItem(BaseModel):
    term: str
    weight: float = 1.0
    aliases: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_item(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"term": data}
        return data


class ProfileSignalCategory(BaseModel):
    items: list[ProfileSignalItem] = Field(default_factory=list)


class MustMatchRule(BaseModel):
    any: list[ProfileSignalItem] = Field(default_factory=list)


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


def _normalize_signal_categories(raw_signals: dict[str, object]) -> dict[str, dict[str, object]]:
    signals: dict[str, dict[str, object]] = {}
    for name, value in raw_signals.items():
        if isinstance(value, list):
            signals[name] = {"items": value}
            continue
        if isinstance(value, dict):
            items = value.get("items", [])
            signals[name] = {"items": items}
    return signals


class CandidateProfile(BaseModel):
    name: str | None = None
    must_match: MustMatchRule = Field(default_factory=MustMatchRule)
    search_queries: dict[str, list[str]] = Field(default_factory=dict)
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
    no_signal_score: int | None = None
    positive_signal_weight: int = 8
    negative_signal_weight: int = -10
    positive_score_scale: float | None = None
    negative_score_scale: float | None = None
    strong_negative_threshold: float | None = None
    strong_negative_score_cap: int | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_signals(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if data.get("signals"):
            migrated = dict(data)
            migrated["signals"] = _normalize_signal_categories(data.get("signals") or {})
            return migrated

        signals: dict[str, dict[str, object]] = {}
        legacy_positive = data.get("positive_signals") or {}
        legacy_negative = data.get("negative_signals") or {}

        legacy_lists: list[tuple[str, list[str]]] = [
            ("interests", data.get("interests") or []),
            ("preferred_domains", data.get("preferred_domains") or []),
            ("strengths", data.get("strengths") or []),
            ("portfolio_projects", data.get("portfolio_projects") or []),
            ("location_preferences", data.get("location_preferences") or []),
            ("disliked_work", data.get("disliked_work") or []),
            ("exclusions", data.get("exclusions") or []),
        ]
        for name, terms in legacy_lists:
            items = _items_from_terms([str(term) for term in terms])
            if items:
                signals[name] = {"items": items}

        positive_items = _items_from_weight_map(legacy_positive)
        if positive_items:
            signals["positive_signals"] = {"items": positive_items}

        negative_items = _items_from_weight_map(legacy_negative)
        if negative_items:
            signals["negative_signals"] = {"items": negative_items}

        migrated = dict(data)
        migrated["signals"] = signals
        return migrated
