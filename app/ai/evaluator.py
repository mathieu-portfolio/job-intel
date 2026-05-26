from __future__ import annotations

import json

from app.llm.base import LlmProvider
from app.models.evaluation import AiJobEvaluation
from app.models.job import JobOffer
from app.models.profile import CandidateProfile


SYSTEM_PROMPT = """You evaluate the semantic and qualitative fit of job offers.
Do not evaluate seniority, location eligibility, contract eligibility, or other structured
hard filters. Those are handled by deterministic rules outside the model.
Focus on whether the posting describes relevant hands-on work, credible technical substance,
domain alignment, portfolio/story alignment, learning value, and posting quality.
Use scores from 0 to 100 where higher is better.
Use recommendation values exactly as: high, medium, low, skip.
Keep recommendation consistent with semantic scores and reasoning. Use suggested_positioning
as null for poor matches."""


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


def build_job_evaluation_prompts(
    job: JobOffer,
    profile: CandidateProfile,
) -> tuple[str, str]:
    user_prompt = json.dumps(
        {
            "candidate_profile": _profile_payload(profile),
            "job": _job_payload(job),
            "instructions": {
                "fit_score": "Overall semantic suitability, excluding seniority and hard eligibility rules.",
                "technical_fit_score": "Whether the role is genuinely aligned with the candidate's technical interests and strengths.",
                "domain_fit_score": "Match with preferred domains and problem spaces.",
                "role_interest_score": "How interesting and hands-on the responsibilities appear for this candidate.",
                "learning_potential_score": "How much useful growth the role may offer.",
                "posting_quality_score": "Specificity, clarity, and trustworthiness of the posting. Higher is better.",
                "portfolio_alignment_score": "Fit with portfolio/project positioning.",
                "summary": "One short sentence summarizing the evaluation.",
                "recommendation": "One of: high, medium, low, skip. Must match the scores and reasoning.",
                "reasoning": "Concrete reasons grounded in the posting.",
                "risks": "Practical concerns or missing information.",
                "suggested_positioning": "How to frame the application, or null for poor matches.",
            },
        },
        ensure_ascii=False,
    )
    return SYSTEM_PROMPT, user_prompt


def evaluate_job_with_ai(
    job: JobOffer,
    profile: CandidateProfile,
    llm_provider: LlmProvider,
    *,
    system_prompt: str | None = None,
    user_prompt: str | None = None,
) -> AiJobEvaluation:
    if system_prompt is None or user_prompt is None:
        system_prompt, user_prompt = build_job_evaluation_prompts(job, profile)

    return llm_provider.generate_structured(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_model=AiJobEvaluation,
    )
