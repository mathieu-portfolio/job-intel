from __future__ import annotations

import json
import os

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError
from pydantic import ValidationError

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


def _client() -> OpenAI:
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and set OPENAI_API_KEY, "
            "or run `python -m app.cli rank --dry-run` to preview jobs without using the API."
        )
    return OpenAI(api_key=api_key)


def evaluate_job_with_ai(job: JobOffer, profile: CandidateProfile) -> AiJobEvaluation:
    client = _client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

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

    try:
        parsed_response = client.beta.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format=AiJobEvaluation,
        )
        parsed = parsed_response.choices[0].message.parsed
        if parsed is None:
            raise RuntimeError("OpenAI returned an empty structured evaluation.")
        return parsed
    except AttributeError:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )
        except OpenAIError as error:
            raise RuntimeError(f"OpenAI request failed: {error}") from error

        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("OpenAI returned an empty evaluation.")
        try:
            return AiJobEvaluation.model_validate_json(content)
        except ValidationError as error:
            raise RuntimeError(f"OpenAI response did not match the evaluation schema: {error}") from error
    except OpenAIError as error:
        raise RuntimeError(f"OpenAI request failed: {error}") from error
    except ValidationError as error:
        raise RuntimeError(f"OpenAI response did not match the evaluation schema: {error}") from error
