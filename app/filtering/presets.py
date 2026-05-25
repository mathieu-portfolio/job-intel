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
        weights=RuleScoringConfig(
            positive_terms={
                "backend": 4,
                "platform": 4,
                "r&d": 5,
            },
            negative_terms={"sales": -8, "cold calling": -12, "unpaid": -20},
        ),
    ),
    ScoringPreset(
        id="safe_match",
        name="Safe Match",
        description="Conservative preset that penalizes mismatch and ambiguity more heavily.",
        weights=RuleScoringConfig(
            positive_terms={"stable": 4, "long-term": 4, "mentorship": 4},
            negative_terms={"senior": -4, "lead": -6, "sales": -10, "unpaid": -24, "commission": -12},
            no_signal_score=15,
            positive_score_scale=2,
            negative_score_scale=5,
            strong_negative_threshold=-16,
            strong_negative_score_cap=5,
        ),
    ),
    ScoringPreset(
        id="high_potential",
        name="High Potential",
        description="Rewards growth potential, learning-heavy roles, and adjacent technical domains.",
        weights=RuleScoringConfig(
            positive_terms={
                "research": 8,
                "robotics": 8,
                "platform": 6,
                "learning": 5,
                "mentorship": 5,
                "r&d": 8,
            },
            negative_terms={"maintenance": -5, "legacy": -4, "support": -8, "sales": -10},
            positive_score_scale=4,
            negative_score_scale=3,
        ),
    ),
    ScoringPreset(
        id="remote_first",
        name="Remote First",
        description="Prioritizes remote-friendly and distributed work.",
        weights=RuleScoringConfig(
            positive_terms={"remote": 16, "hybrid": 7, "distributed": 8, "async": 5, "work from home": 14},
            negative_terms={"on-site": -14, "onsite": -14, "relocation": -10, "commute": -8},
            no_signal_score=18,
        ),
    ),
    ScoringPreset(
        id="compensation_focused",
        name="Compensation Focused",
        description="Prioritizes explicit compensation, seniority leverage, and benefits.",
        weights=RuleScoringConfig(
            positive_terms={
                "salary": 10,
                "equity": 7,
                "bonus": 6,
                "benefits": 5,
                "senior": 5,
                "stock options": 8,
            },
            negative_terms={"unpaid": -30, "internship": -12, "volunteer": -24},
            no_signal_score=12,
            positive_score_scale=4,
        ),
    ),
    ScoringPreset(
        id="engineering_quality",
        name="Engineering Quality",
        description="Rewards strong engineering practice, systems depth, and code quality signals.",
        weights=RuleScoringConfig(
            positive_terms={
                "architecture": 8,
                "testing": 7,
                "observability": 6,
                "performance": 7,
                "code quality": 8,
            },
            negative_terms={"wordpress": -6, "cms": -5, "no-code": -10, "support": -8},
        ),
    ),
    ScoringPreset(
        id="fast_apply",
        name="Fast Apply",
        description="Favors clear, practical matches likely to be quick applications.",
        weights=RuleScoringConfig(
            positive_terms={"easy apply": 12, "quick apply": 12, "remote": 7, "hybrid": 5},
            negative_terms={"cover letter": -6, "assessment": -8, "security clearance": -12, "relocation": -8},
            no_signal_score=25,
            positive_score_scale=3,
            negative_score_scale=3,
        ),
    ),
)


def builtin_preset_by_id(preset_id: str) -> ScoringPreset:
    for preset in BUILTIN_SCORING_PRESETS:
        if preset.id == preset_id:
            return preset
    raise ValueError(f"Unknown scoring preset: {preset_id}")
