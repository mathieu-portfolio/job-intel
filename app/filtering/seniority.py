from __future__ import annotations

import re
import unicodedata

from app.models.evaluation import SeniorityEvaluation, SeniorityLevel
from app.models.job import JobOffer
from app.models.profile import CandidateProfile


LEVEL_ORDER: dict[SeniorityLevel, int] = {
    "internship": 0,
    "junior": 1,
    "mid": 2,
    "senior": 3,
    "lead": 4,
    "unknown": -1,
}

SENIORITY_ALIASES: dict[SeniorityLevel, tuple[str, ...]] = {
    "internship": (
        "intern",
        "internship",
        "trainee",
        "stage",
        "stagiaire",
        "alternance",
        "apprenticeship",
        "apprenti",
    ),
    "junior": (
        "junior",
        "jr",
        "entry level",
        "entry-level",
        "graduate",
        "new grad",
        "debutant",
        "débutant",
        "premier emploi",
        "0 2 ans",
        "0-2 ans",
        "1 2 ans",
        "1-2 ans",
    ),
    "mid": (
        "mid",
        "middle",
        "confirmed",
        "confirmé",
        "confirme",
        "intermediate",
        "2 5 ans",
        "2-5 ans",
        "3 5 ans",
        "3-5 ans",
        "3 ans",
    ),
    "senior": (
        "senior",
        "sr",
        "experienced",
        "expérimenté",
        "experimente",
        "5 ans",
        "5+ ans",
        "6 ans",
        "7 ans",
    ),
    "lead": (
        "lead",
        "staff",
        "principal",
        "architect",
        "manager",
        "head of",
        "responsable",
        "tech lead",
        "chef de projet",
    ),
}


def normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    without_accents = "".join(character for character in decomposed if not unicodedata.combining(character))
    punctuation_spaced = re.sub(r"[^\w\s+#]", " ", without_accents)
    return re.sub(r"\s+", " ", punctuation_spaced).strip()


def normalize_seniority(value: str | None) -> SeniorityLevel:
    if not value:
        return "unknown"
    normalized = normalize_text(value)
    for level, aliases in SENIORITY_ALIASES.items():
        if any(_contains_alias(normalized, alias) for alias in aliases):
            return level
    if normalized in LEVEL_ORDER:
        return normalized  # type: ignore[return-value]
    return "unknown"


def detect_offer_seniority(job: JobOffer) -> tuple[SeniorityLevel, int, list[str]]:
    title_text = normalize_text(job.title)
    body_text = normalize_text(" ".join([job.title, job.description, " ".join(job.tags)]))

    title_matches = _matches_by_level(title_text)
    if title_matches:
        level = _highest_priority_match(title_matches)
        return level, 90, [f"Detected offer seniority `{level}` from the title."]

    body_matches = _matches_by_level(body_text)
    if body_matches:
        level = _highest_priority_match(body_matches)
        return level, 65, [f"Detected offer seniority `{level}` from the description/tags."]

    return "unknown", 30, ["No explicit seniority signal detected in the offer."]


def evaluate_seniority(job: JobOffer, profile: CandidateProfile | None) -> SeniorityEvaluation:
    target = normalize_seniority(profile.target_seniority if profile is not None else None)
    offer, confidence, reasoning = detect_offer_seniority(job)

    if target == "unknown" or offer == "unknown":
        return SeniorityEvaluation(
            target_seniority=target,
            offer_seniority=offer,
            score=70,
            confidence=confidence if offer != "unknown" else min(confidence, 40),
            reasoning=[
                *reasoning,
                "Using neutral seniority score because the target or offer seniority is unknown.",
            ],
        )

    target_rank = LEVEL_ORDER[target]
    offer_rank = LEVEL_ORDER[offer]
    distance = offer_rank - target_rank

    if distance == 0:
        score = 100
        reason = "Offer seniority matches the candidate target seniority."
    elif distance == 1:
        score = 85
        reason = "Offer is one level above target, treated as a reasonable stretch."
    elif distance == -1:
        score = 60
        reason = "Offer is one level below target."
    elif distance > 1:
        score = 35
        reason = "Offer is more than one level above target."
    else:
        score = 25
        reason = "Offer is more than one level below target."

    return SeniorityEvaluation(
        target_seniority=target,
        offer_seniority=offer,
        score=score,
        confidence=confidence,
        reasoning=[*reasoning, reason],
    )


def _matches_by_level(text: str) -> dict[SeniorityLevel, list[str]]:
    matches: dict[SeniorityLevel, list[str]] = {}
    for level, aliases in SENIORITY_ALIASES.items():
        matched_aliases = [alias for alias in aliases if _contains_alias(text, alias)]
        if matched_aliases:
            matches[level] = matched_aliases
    return matches


def _highest_priority_match(matches: dict[SeniorityLevel, list[str]]) -> SeniorityLevel:
    # Prefer the most senior explicit signal if multiple are present.
    return max(matches, key=lambda level: LEVEL_ORDER[level])


def _contains_alias(normalized_text: str, alias: str) -> bool:
    normalized_alias = normalize_text(alias)
    if not normalized_alias:
        return False
    escaped = re.escape(normalized_alias)
    return re.search(rf"(?<![\w+#]){escaped}(?![\w+#])", normalized_text) is not None
