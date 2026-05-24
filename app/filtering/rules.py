from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from app.ai.decision import recommendation_from_score
from app.models.evaluation import RuleEvaluation, WeightedTermMatch
from app.models.job import JobOffer
from app.models.profile import CandidateProfile


class RuleScoringConfig(BaseModel):
    positive_terms: dict[str, int] = Field(
        default_factory=lambda: {
            "c++": 14,
            "cpp": 10,
            "simulation": 12,
            "systems": 10,
            "linux": 8,
            "embedded": 8,
            "graphics": 8,
            "rendering": 8,
            "tooling": 7,
            "infrastructure": 6,
            "performance": 8,
        }
    )
    negative_terms: dict[str, int] = Field(
        default_factory=lambda: {
            "frontend": -8,
            "react": -8,
            "php": -10,
            "wordpress": -12,
            "salesforce": -12,
            "senior": -16,
            "lead": -14,
            "principal": -18,
            "staff": -16,
        }
    )
    profile_positive_weight: int = 8
    profile_negative_weight: int = -10
    neutral_score: int = 50
    score_scale: int = 2


def load_rule_scoring_config(path: Path | None = None) -> RuleScoringConfig:
    if path is None:
        return RuleScoringConfig()
    try:
        raw_config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimeError(f"Rule weights file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Rule weights file is not valid JSON: {path}") from error
    try:
        return RuleScoringConfig.model_validate(raw_config)
    except ValidationError as error:
        raise RuntimeError(f"Rule weights file has invalid fields: {path}") from error


def _contains_term(text: str, term: str) -> bool:
    escaped = re.escape(term.lower())
    return re.search(rf"(?<!\w){escaped}(?!\w)", text) is not None


def _profile_positive_terms(profile: CandidateProfile | None) -> list[str]:
    if profile is None:
        return POSITIVE_TERMS

    terms = [
        *profile.interests,
        *profile.preferred_domains,
        *profile.strengths,
        *profile.portfolio_projects,
    ]
    return [term.lower() for term in terms if term.strip()]


def _profile_negative_terms(profile: CandidateProfile | None) -> list[str]:
    if profile is None:
        return NEGATIVE_TERMS

    terms = [
        *profile.disliked_work,
    ]
    if profile.target_seniority and profile.target_seniority.lower() in {"junior", "entry", "entry-level"}:
        terms.extend(["senior", "lead", "principal", "staff"])

    return [term.lower() for term in terms if term.strip()]


def _term_weights(
    *,
    configured_terms: dict[str, int],
    profile_terms: list[str],
    default_profile_weight: int,
) -> dict[str, int]:
    weights = {term.lower(): weight for term, weight in configured_terms.items()}
    for term in profile_terms:
        weights.setdefault(term.lower(), default_profile_weight)
    return weights


def _normalized_score(raw_score: int, config: RuleScoringConfig) -> int:
    return max(0, min(100, config.neutral_score + (raw_score * config.score_scale)))


def evaluate_job(
    job: JobOffer,
    profile: CandidateProfile | None = None,
    config: RuleScoringConfig | None = None,
) -> RuleEvaluation:
    config = config or RuleScoringConfig()
    text = " ".join(
        [
            job.title,
            job.company,
            job.location or "",
            job.description,
            " ".join(job.tags),
        ]
    ).lower()

    positive_weights = _term_weights(
        configured_terms=config.positive_terms,
        profile_terms=_profile_positive_terms(profile),
        default_profile_weight=config.profile_positive_weight,
    )
    negative_weights = _term_weights(
        configured_terms=config.negative_terms,
        profile_terms=_profile_negative_terms(profile),
        default_profile_weight=config.profile_negative_weight,
    )
    positives = [
        WeightedTermMatch(term=term, weight=weight)
        for term, weight in positive_weights.items()
        if _contains_term(text, term)
    ]
    negatives = [
        WeightedTermMatch(term=term, weight=weight)
        for term, weight in negative_weights.items()
        if _contains_term(text, term)
    ]

    score = sum(match.weight for match in positives) + sum(match.weight for match in negatives)
    normalized_score = _normalized_score(score, config)
    reasoning = [
        f"Matched {len(positives)} positive weighted terms.",
        f"Matched {len(negatives)} negative weighted terms.",
    ]

    return RuleEvaluation(
        score=score,
        normalized_score=normalized_score,
        matched_positive_terms=positives,
        matched_negative_terms=negatives,
        decision=recommendation_from_score(normalized_score),
        reasoning=reasoning,
    )


def filter_jobs(
    jobs: list[JobOffer],
    min_score: int = 10,
    profile: CandidateProfile | None = None,
    config: RuleScoringConfig | None = None,
) -> list[tuple[JobOffer, RuleEvaluation]]:
    evaluated = [(job, evaluate_job(job, profile=profile, config=config)) for job in jobs]
    matches = [(job, evaluation) for job, evaluation in evaluated if evaluation.score >= min_score]
    return sorted(matches, key=lambda item: item[1].normalized_score, reverse=True)
