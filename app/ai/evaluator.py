from __future__ import annotations

import json

from app.llm.base import LlmProvider
from app.models.evaluation import AiJobEvaluation
from app.models.job import JobOffer
from app.models.profile import CandidateProfile


SYSTEM_PROMPT = """You evaluate job offers for a junior technical candidate.
Return practical, evidence-based judgments. Penalize vague buzzword-heavy posts,
senior-only roles, unclear engineering substance, and poor portfolio alignment.
Use scores from 0 to 100 where higher is better, except wording_risk_score where
higher means more vague or bullshit wording risk."""


def _job_payload(job: JobOffer) -> dict[str, object]:
    return {
        "source": job.source,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "remote": job.remote,
        "salary": job.salary,
        "tags": job.tags,
        "url": str(job.url),
        "description": job.description[:5000],
    }


def _profile_payload(profile: CandidateProfile) -> dict[str, object]:
    return profile.model_dump(mode="json")


def evaluate_job_with_ai(
    job: JobOffer,
    profile: CandidateProfile,
    llm_provider: LlmProvider,
) -> AiJobEvaluation:
    user_prompt = json.dumps(
        {
            "candidate_profile": _profile_payload(profile),
            "job": _job_payload(job),
            "instructions": {
                "fit_score": "Overall suitability for this candidate.",
                "technical_fit_score": "Match to technical interests and strengths.",
                "junior_accessibility_score": "Likelihood a junior candidate can credibly apply.",
                "learning_potential_score": "How much useful growth the role may offer.",
                "wording_risk_score": "Higher means vaguer, buzzword-heavy, or less trustworthy.",
                "portfolio_alignment_score": "Fit with portfolio/project positioning.",
                "recommendation": "One of: strong_apply, apply, consider, skip.",
                "reasoning": "Concrete reasons grounded in the posting.",
                "risks": "Practical concerns or missing information.",
                "suggested_positioning": "How the candidate should frame themselves if applying.",
            },
        },
        ensure_ascii=False,
    )

    return llm_provider.generate_structured(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        response_model=AiJobEvaluation,
    )
