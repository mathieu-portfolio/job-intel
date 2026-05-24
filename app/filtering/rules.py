from __future__ import annotations

import re

from app.models.evaluation import RuleEvaluation
from app.models.job import JobOffer


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


def evaluate_job(job: JobOffer) -> RuleEvaluation:
    text = " ".join(
        [
            job.title,
            job.company,
            job.location or "",
            job.description,
            " ".join(job.tags),
        ]
    ).lower()

    positives = [term for term in POSITIVE_TERMS if _contains_term(text, term)]
    negatives = [term for term in NEGATIVE_TERMS if _contains_term(text, term)]

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
) -> list[tuple[JobOffer, RuleEvaluation]]:
    evaluated = [(job, evaluate_job(job)) for job in jobs]
    matches = [(job, evaluation) for job, evaluation in evaluated if evaluation.score >= min_score]
    return sorted(matches, key=lambda item: item[1].score, reverse=True)
