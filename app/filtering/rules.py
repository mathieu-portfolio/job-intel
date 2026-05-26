from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from app.models.evaluation import RuleEvaluation, WeightedTermMatch, recommendation_from_score
from app.models.job import JobOffer
from app.models.profile import CandidateProfile, MustMatchRule, ProfileSignalItem
from app.filtering.seniority import evaluate_seniority

DEFAULT_RULE_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "scoring_presets" / "balanced.json"
)


class RuleScoringConfig(BaseModel):
    positive_terms: dict[str, int] = Field(default_factory=dict)
    negative_terms: dict[str, int] = Field(default_factory=dict)
    must_match: MustMatchRule = Field(default_factory=MustMatchRule)
    category_weights: dict[str, float]
    no_signal_score: int
    positive_score_scale: float
    negative_score_scale: float
    strong_negative_threshold: float
    strong_negative_score_cap: int


@dataclass(frozen=True)
class Alias:
    text: str
    language: str | None = None


@dataclass(frozen=True)
class MatchResult:
    canonical_term: str
    matched_alias: str
    language: str | None = None


def load_rule_scoring_config(path: Path | None = None) -> RuleScoringConfig:
    if path is None:
        path = DEFAULT_RULE_CONFIG_PATH
    try:
        raw_config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimeError(f"Rule weights file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Rule weights file is not valid JSON: {path}") from error
    return parse_rule_scoring_config(raw_config, source=str(path))


def parse_rule_scoring_config(raw_config: object, *, source: str = "rule scoring config") -> RuleScoringConfig:
    if isinstance(raw_config, dict) and isinstance(raw_config.get("weights"), dict):
        raw_config = raw_config["weights"]
    try:
        return RuleScoringConfig.model_validate(raw_config)
    except ValidationError as error:
        raise RuntimeError(f"Rule weights config has invalid fields: {source}") from error


def normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    without_accents = "".join(character for character in decomposed if not unicodedata.combining(character))
    punctuation_spaced = re.sub(r"[^\w\s+#]", " ", without_accents)
    return re.sub(r"\s+", " ", punctuation_spaced).strip()


def _contains_normalized_term(normalized_text: str, normalized_term: str) -> bool:
    if not normalized_term:
        return False
    escaped = re.escape(normalized_term)
    return re.search(rf"(?<![\w+#]){escaped}(?![\w+#])", normalized_text) is not None


def _contains_term(text: str, term: str) -> bool:
    return _contains_normalized_term(normalize_text(text), normalize_text(term))


def _alias_cache_key(item: ProfileSignalItem) -> str:
    return json.dumps(item.aliases, sort_keys=True, ensure_ascii=False)


@lru_cache(maxsize=4096)
def _normalized_aliases_for_item(term: str, aliases_key: str) -> tuple[tuple[str, str, str | None], ...]:
    aliases = json.loads(aliases_key) if aliases_key else {}
    normalized_aliases: list[tuple[str, str, str | None]] = []
    if isinstance(aliases, dict):
        for language, values in aliases.items():
            if isinstance(values, list):
                normalized_aliases.extend(
                    (normalize_text(str(alias)), str(alias), str(language))
                    for alias in values
                    if str(alias).strip()
                )
    if not normalized_aliases:
        normalized_aliases.append((normalize_text(term), term, None))
    return tuple(normalized_aliases)


def match_signal_item(text: str, item: ProfileSignalItem) -> MatchResult | None:
    normalized_text = normalize_text(text)
    for normalized_alias, alias_text, language in _normalized_aliases_for_item(item.term, _alias_cache_key(item)):
        if _contains_normalized_term(normalized_text, normalized_alias):
            return MatchResult(
                canonical_term=item.term,
                matched_alias=alias_text,
                language=language,
            )
    return None


def precompute_rule_matching(profile: CandidateProfile | None, configs: list[RuleScoringConfig]) -> None:
    items: list[ProfileSignalItem] = []
    if profile is not None:
        items.extend(profile.must_match.any)
        for category in profile.signals.values():
            items.extend(category.items)
    for config in configs:
        items.extend(config.must_match.any)
    for item in items:
        _normalized_aliases_for_item(item.term, _alias_cache_key(item))


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


def evaluate_job(
    job: JobOffer,
    profile: CandidateProfile | None = None,
    config: RuleScoringConfig | None = None,
) -> RuleEvaluation:
    config = config or load_rule_scoring_config()
    if profile is not None:
        overrides = {
            "no_signal_score": profile.no_signal_score,
            "positive_score_scale": profile.positive_score_scale,
            "negative_score_scale": profile.negative_score_scale,
            "strong_negative_threshold": profile.strong_negative_threshold,
            "strong_negative_score_cap": profile.strong_negative_score_cap,
        }
        config = config.model_copy(
            update={key: value for key, value in overrides.items() if value is not None}
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
        *seniority_evaluation.reasoning,
        f"Calibrated raw score {score:+.2f} to {normalized_score}/100.",
    ]

    return RuleEvaluation(
        score=round(score),
        normalized_score=normalized_score,
        matched_positive_terms=positives,
        matched_negative_terms=negatives,
        decision=recommendation_from_score(normalized_score),
        reasoning=reasoning,
        seniority=seniority_evaluation,
    )
