from __future__ import annotations

import re

from app.models.evaluation import RuleEvaluation
from app.models.job import JobOffer
from app.models.profile import CandidateProfile


POSITIVE_TERMS = [
    "c++",
    "cpp",
    "simulation",
    "systems",
    "linux",
    "embedded",
    "graphics",
    "rendering",
    "tooling",
    "infrastructure",
    "performance",
]

NEGATIVE_TERMS = [
    "frontend",
    "react",
    "php",
    "wordpress",
    "salesforce",
    "senior",
    "lead",
    "principal",
]


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


def evaluate_job(job: JobOffer, profile: CandidateProfile | None = None) -> RuleEvaluation:
    text = " ".join(
        [
            job.title,
            job.company,
            job.location or "",
            job.description,
            " ".join(job.tags),
        ]
    ).lower()

    positives = [term for term in _profile_positive_terms(profile) if _contains_term(text, term)]
    negatives = [term for term in _profile_negative_terms(profile) if _contains_term(text, term)]

    score = len(positives) * 10 - len(negatives) * 8

    if score >= 20:
        decision = "high"
    elif score >= 10:
        decision = "maybe"
    else:
        decision = "skip"

    return RuleEvaluation(
        score=score,
        matched_positive_terms=positives,
        matched_negative_terms=negatives,
        decision=decision,
    )


def filter_jobs(
    jobs: list[JobOffer],
    min_score: int = 10,
    profile: CandidateProfile | None = None,
) -> list[tuple[JobOffer, RuleEvaluation]]:
    evaluated = [(job, evaluate_job(job, profile=profile)) for job in jobs]
    matches = [(job, evaluation) for job, evaluation in evaluated if evaluation.score >= min_score]
    return sorted(matches, key=lambda item: item[1].score, reverse=True)
