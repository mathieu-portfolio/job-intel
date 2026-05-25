from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from app.models.evaluation import RuleEvaluation, WeightedTermMatch, recommendation_from_score
from app.models.job import JobOffer
from app.models.profile import CandidateProfile

DEFAULT_CATEGORY_WEIGHTS: dict[str, float] = {
    "interests": 0.25,
    "preferred_domains": 0.15,
    "strengths": 0.20,
    "portfolio_projects": 0.15,
    "location_preferences": 0.15,
    "disliked_work": -0.20,
    "exclusions": -0.80,
    "positive_signals": 0.50,
    "negative_signals": -0.50,
}


class RuleScoringConfig(BaseModel):
    positive_terms: dict[str, int] = Field(default_factory=dict)
    negative_terms: dict[str, int] = Field(default_factory=dict)
    category_weights: dict[str, float] = Field(default_factory=dict)
    profile_positive_weight: int = 8
    profile_negative_weight: int = -10
    no_signal_score: int = 20
    positive_score_scale: float = 80
    negative_score_scale: float = 80
    strong_negative_threshold: int = -20
    strong_negative_score_cap: int = 10


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


def _normalized_score(
    *,
    positive_score: float,
    negative_score: float,
    config: RuleScoringConfig,
) -> int:
    raw_score = positive_score + negative_score
    score = (
        config.no_signal_score
        + (positive_score * config.positive_score_scale)
        + (negative_score * config.negative_score_scale)
    )
    if raw_score <= config.strong_negative_threshold:
        score = min(score, config.strong_negative_score_cap)
    return max(0, min(100, round(score)))


def _configured_term_matches(
    *,
    text: str,
    terms: dict[str, int],
) -> list[WeightedTermMatch]:
    return [
        WeightedTermMatch(term=term.lower(), weight=float(weight))
        for term, weight in terms.items()
        if _contains_term(text, term)
    ]


def _profile_signal_matches(
    *,
    text: str,
    profile: CandidateProfile | None,
    config: RuleScoringConfig,
) -> tuple[list[WeightedTermMatch], list[WeightedTermMatch], float, float, list[str]]:
    if profile is None:
        return [], [], 0.0, 0.0, []

    positives: list[WeightedTermMatch] = []
    negatives: list[WeightedTermMatch] = []
    positive_score = 0.0
    negative_score = 0.0
    reasoning: list[str] = []

    for category_name, category in profile.signals.items():
        category_weight = config.category_weights.get(
            category_name,
            DEFAULT_CATEGORY_WEIGHTS.get(category_name, 0.0),
        )
        if category_weight == 0:
            continue
        total_item_weight = sum(abs(item.weight) for item in category.items if item.term.strip())
        if total_item_weight <= 0:
            continue
        matched_items = [
            item
            for item in category.items
            if item.term.strip() and _contains_term(text, item.term)
        ]
        matched_weight = sum(abs(item.weight) for item in matched_items)
        category_score = matched_weight / total_item_weight
        contribution = category_score * category_weight
        if category_weight >= 0:
            positive_score += contribution
            positives.extend(
                WeightedTermMatch(term=item.term.lower(), weight=contribution * 100)
                for item in matched_items
            )
        else:
            negative_score += contribution
            negatives.extend(
                WeightedTermMatch(term=item.term.lower(), weight=contribution * 100)
                for item in matched_items
            )
        if matched_items:
            reasoning.append(
                f"Matched {len(matched_items)}/{len(category.items)} items in {category_name} "
                f"for {contribution:+.2f}."
            )

    return positives, negatives, positive_score, negative_score, reasoning


def evaluate_job(
    job: JobOffer,
    profile: CandidateProfile | None = None,
    config: RuleScoringConfig | None = None,
) -> RuleEvaluation:
    config = config or RuleScoringConfig()
    if profile is not None:
        config = config.model_copy(
            update={
                "no_signal_score": profile.no_signal_score,
                "positive_score_scale": profile.positive_score_scale,
                "negative_score_scale": profile.negative_score_scale,
                "strong_negative_threshold": profile.strong_negative_threshold,
                "strong_negative_score_cap": profile.strong_negative_score_cap,
            }
        )
    text = " ".join(
        [
            job.title,
            job.company,
            job.location or "",
            job.description,
            " ".join(job.tags),
        ]
    ).lower()

    configured_positives = _configured_term_matches(text=text, terms=config.positive_terms)
    configured_negatives = _configured_term_matches(text=text, terms=config.negative_terms)
    profile_positives, profile_negatives, profile_positive_score, profile_negative_score, profile_reasoning = (
        _profile_signal_matches(text=text, profile=profile, config=config)
    )
    positives = [*configured_positives, *profile_positives]
    negatives = [*configured_negatives, *profile_negatives]

    positive_score = sum(match.weight for match in configured_positives) + profile_positive_score
    negative_score = sum(match.weight for match in configured_negatives) + profile_negative_score
    score = positive_score + negative_score
    normalized_score = _normalized_score(
        positive_score=positive_score,
        negative_score=negative_score,
        config=config,
    )
    reasoning = [
        f"Matched {len(positives)} positive weighted terms for {positive_score:+.2f}.",
        f"Matched {len(negatives)} negative weighted terms for {negative_score:+.2f}.",
        *profile_reasoning,
        f"Calibrated raw score {score:+.2f} to {normalized_score}/100.",
    ]

    return RuleEvaluation(
        score=round(score),
        normalized_score=normalized_score,
        matched_positive_terms=positives,
        matched_negative_terms=negatives,
        decision=recommendation_from_score(normalized_score),
        reasoning=reasoning,
    )


def filter_jobs(
    jobs: list[JobOffer],
    min_score: int = 40,
    profile: CandidateProfile | None = None,
    config: RuleScoringConfig | None = None,
) -> list[tuple[JobOffer, RuleEvaluation]]:
    evaluated = [(job, evaluate_job(job, profile=profile, config=config)) for job in jobs]
    matches = [
        (job, evaluation)
        for job, evaluation in evaluated
        if evaluation.normalized_score >= min_score
    ]
    return sorted(matches, key=lambda item: item[1].normalized_score, reverse=True)
