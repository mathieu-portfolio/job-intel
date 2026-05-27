from __future__ import annotations

from app.models.evaluation import RuleEvaluation, WeightedTermMatch, recommendation_from_score
from app.models.job import JobOffer
from app.models.profile import CandidateProfile, ProfileSignalItem
from app.filtering.seniority import evaluate_seniority
from app.filtering.rule_config import RuleScoringConfig, load_rule_scoring_config
from app.filtering.rule_matching import _contains_term, match_signal_item

def _must_match_terms(
    *,
    config: RuleScoringConfig,
    profile: CandidateProfile | None,
) -> list[ProfileSignalItem]:
    terms: list[ProfileSignalItem] = []
    terms.extend(term for term in config.must_match.any if term.term.strip())
    if profile is not None:
        terms.extend(term for term in profile.must_match.any if term.term.strip())
    return terms


def _must_match_failure(
    *,
    text: str,
    config: RuleScoringConfig,
    profile: CandidateProfile | None,
) -> str | None:
    terms = _must_match_terms(config=config, profile=profile)
    if not terms:
        return None
    if any(match_signal_item(text, term) for term in terms):
        return None
    canonical_terms = ", ".join(term.term for term in terms)
    return f"Rejected because none of the must_match.any terms matched: {canonical_terms}."


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
        WeightedTermMatch(
            category="configured",
            term=term,
            matched_alias=term,
            language=None,
            weight=float(weight),
            contribution=float(weight),
        )
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
        category_weight = config.category_weights.get(category_name, 0.0)
        if category_weight == 0:
            continue
        total_item_weight = sum(abs(item.weight) for item in category.items if item.term.strip())
        if total_item_weight <= 0:
            continue
        matched_items = [
            (item, match)
            for item in category.items
            if item.term.strip()
            for match in [match_signal_item(text, item)]
            if match is not None
        ]
        matched_weight = sum(abs(item.weight) for item, _ in matched_items)
        category_score = matched_weight / total_item_weight
        contribution = category_score * category_weight
        if category_weight >= 0:
            positive_score += contribution
            positives.extend(
                WeightedTermMatch(
                    category=category_name,
                    term=item.term,
                    matched_alias=match.matched_alias,
                    language=match.language,
                    weight=contribution * 100,
                    contribution=contribution,
                )
                for item, match in matched_items
            )
        else:
            negative_score += contribution
            negatives.extend(
                WeightedTermMatch(
                    category=category_name,
                    term=item.term,
                    matched_alias=match.matched_alias,
                    language=match.language,
                    weight=contribution * 100,
                    contribution=contribution,
                )
                for item, match in matched_items
            )
        if matched_items:
            reasoning.append(
                f"Matched {len(matched_items)}/{len(category.items)} items in {category_name} "
                f"for {contribution:+.2f}."
            )

    return positives, negatives, positive_score, negative_score, reasoning



def evaluate_job_profile_match(
    job: JobOffer,
    profile: CandidateProfile | None = None,
    config: RuleScoringConfig | None = None,
) -> RuleEvaluation:
    """Compute preset-independent profile/offer match facts.

    The stored contributions are per-category match ratios in the 0..1 range.
    Presets can later apply their own category weights at runtime without
    recomputing textual matches or storing one score per preset.
    """
    config = config or load_rule_scoring_config()
    text = " ".join(
        [
            job.title,
            job.company,
            job.location or "",
            job.description,
            " ".join(job.tags),
        ]
    ).lower()
    seniority_evaluation = evaluate_seniority(job, profile)

    must_match_failure = _must_match_failure(text=text, config=config, profile=profile)
    if must_match_failure:
        return RuleEvaluation(
            score=0,
            normalized_score=0,
            matched_positive_terms=[],
            matched_negative_terms=[],
            decision="skip",
            reasoning=[must_match_failure, *seniority_evaluation.reasoning],
            seniority=seniority_evaluation,
        )

    if profile is None:
        return RuleEvaluation(
            score=0,
            normalized_score=config.no_signal_score,
            matched_positive_terms=[],
            matched_negative_terms=[],
            decision=recommendation_from_score(config.no_signal_score),
            reasoning=["No profile was provided.", *seniority_evaluation.reasoning],
            seniority=seniority_evaluation,
        )

    positives: list[WeightedTermMatch] = []
    negatives: list[WeightedTermMatch] = []
    reasoning: list[str] = []
    for category_name, category in profile.signals.items():
        total_item_weight = sum(abs(item.weight) for item in category.items if item.term.strip())
        if total_item_weight <= 0:
            continue
        matched_items = [
            (item, match)
            for item in category.items
            if item.term.strip()
            for match in [match_signal_item(text, item)]
            if match is not None
        ]
        if not matched_items:
            continue
        category_weight = config.category_weights.get(category_name, 0.0)
        destination = positives if category_weight >= 0 else negatives
        category_score = sum(abs(item.weight) for item, _ in matched_items) / total_item_weight
        destination.extend(
            WeightedTermMatch(
                category=category_name,
                term=item.term,
                matched_alias=match.matched_alias,
                language=match.language,
                weight=(abs(item.weight) / total_item_weight) * 100,
                contribution=abs(item.weight) / total_item_weight,
            )
            for item, match in matched_items
        )
        reasoning.append(
            f"Matched {len(matched_items)}/{len(category.items)} items in {category_name} "
            f"for category ratio {category_score:.2f}."
        )

    return RuleEvaluation(
        score=0,
        normalized_score=config.no_signal_score,
        matched_positive_terms=positives,
        matched_negative_terms=negatives,
        decision=recommendation_from_score(config.no_signal_score),
        reasoning=[*reasoning, *seniority_evaluation.reasoning],
        seniority=seniority_evaluation,
    )


def score_profile_match(
    profile_match: RuleEvaluation,
    config: RuleScoringConfig | None = None,
) -> RuleEvaluation:
    """Apply a scoring preset to stored profile-match facts."""
    config = config or load_rule_scoring_config()
    category_scores: dict[str, float] = {}
    for match in [*profile_match.matched_positive_terms, *profile_match.matched_negative_terms]:
        if not match.category:
            continue
        category_scores[match.category] = category_scores.get(match.category, 0.0) + float(match.contribution or 0.0)

    positive_score = 0.0
    negative_score = 0.0
    for category_name, category_score in category_scores.items():
        category_weight = config.category_weights.get(category_name, 0.0)
        contribution = category_score * category_weight
        if category_weight >= 0:
            positive_score += contribution
        else:
            negative_score += contribution

    raw_score = positive_score + negative_score
    normalized_score = _normalized_score(
        positive_score=positive_score,
        negative_score=negative_score,
        config=config,
    )
    reasoning = [
        f"Applied preset weights to {len(category_scores)} matched categories.",
        f"Calibrated raw score {raw_score:+.2f} to {normalized_score}/100.",
        *profile_match.reasoning,
    ]
    return RuleEvaluation(
        score=round(raw_score),
        normalized_score=normalized_score,
        matched_positive_terms=profile_match.matched_positive_terms,
        matched_negative_terms=profile_match.matched_negative_terms,
        decision=recommendation_from_score(normalized_score),
        reasoning=reasoning,
        seniority=profile_match.seniority,
    )


def evaluate_job(
    job: JobOffer,
    profile: CandidateProfile | None = None,
    config: RuleScoringConfig | None = None,
) -> RuleEvaluation:
    config = config or load_rule_scoring_config()
    profile_match = evaluate_job_profile_match(job, profile=profile, config=config)
    if profile_match.normalized_score == 0 and profile_match.decision == "skip":
        return profile_match
    return score_profile_match(profile_match, config=config)
