from __future__ import annotations

from dataclasses import dataclass

from app.filtering.rules import RuleScoringConfig


@dataclass(frozen=True)
class ScoringPreset:
    id: str
    name: str
    description: str
    weights: RuleScoringConfig
    is_builtin: bool = True
    enabled: bool = True


BUILTIN_SCORING_PRESETS: tuple[ScoringPreset, ...] = (
    ScoringPreset(
        id="balanced",
        name="Balanced",
        description="General fit across profile match, technical relevance, and risk.",
        weights=RuleScoringConfig(),
    ),
    ScoringPreset(
        id="safe_match",
        name="Safe Match",
        description="Conservative preset that penalizes mismatch and ambiguity more heavily.",
        weights=RuleScoringConfig(
            category_weights={
                "interests": 0.20,
                "preferred_domains": 0.15,
                "strengths": 0.15,
                "portfolio_projects": 0.10,
                "disliked_work": -0.30,
                "exclusions": -0.90,
                "negative_signals": -0.40,
            },
            no_signal_score=15,
            strong_negative_threshold=-16,
            strong_negative_score_cap=5,
        ),
    ),
    ScoringPreset(
        id="high_potential",
        name="High Potential",
        description="Rewards growth potential, learning-heavy roles, and adjacent technical domains.",
        weights=RuleScoringConfig(
            category_weights={
                "interests": 0.30,
                "preferred_domains": 0.20,
                "strengths": 0.15,
                "portfolio_projects": 0.25,
                "disliked_work": -0.15,
            },
        ),
    ),
    ScoringPreset(
        id="remote_first",
        name="Remote First",
        description="Prioritizes remote-friendly and distributed work.",
        weights=RuleScoringConfig(
            category_weights={
                "location_preferences": 0.35,
                "interests": 0.15,
                "preferred_domains": 0.10,
                "disliked_work": -0.20,
            },
            no_signal_score=18,
        ),
    ),
    ScoringPreset(
        id="compensation_focused",
        name="Compensation Focused",
        description="Prioritizes explicit compensation, seniority leverage, and benefits.",
        weights=RuleScoringConfig(
            category_weights={
                "compensation": 0.45,
                "interests": 0.10,
                "preferred_domains": 0.10,
                "disliked_work": -0.25,
                "negative_signals": -0.35,
            },
            no_signal_score=12,
        ),
    ),
    ScoringPreset(
        id="engineering_quality",
        name="Engineering Quality",
        description="Rewards strong engineering practice, systems depth, and code quality signals.",
        weights=RuleScoringConfig(
            category_weights={
                "strengths": 0.30,
                "portfolio_projects": 0.25,
                "interests": 0.20,
                "preferred_domains": 0.10,
                "disliked_work": -0.20,
            },
        ),
    ),
    ScoringPreset(
        id="fast_apply",
        name="Fast Apply",
        description="Favors clear, practical matches likely to be quick applications.",
        weights=RuleScoringConfig(
            category_weights={
                "fast_apply": 0.35,
                "location_preferences": 0.20,
                "interests": 0.15,
                "disliked_work": -0.20,
            },
            no_signal_score=25,
        ),
    ),
)


def builtin_preset_by_id(preset_id: str) -> ScoringPreset:
    for preset in BUILTIN_SCORING_PRESETS:
        if preset.id == preset_id:
            return preset
    raise ValueError(f"Unknown scoring preset: {preset_id}")
