from __future__ import annotations

import json

from app.llm.base import StructuredModel


class MockLlmProvider:
    name = "mock"
    model_name = "deterministic-local"
    timeout_seconds = None

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[StructuredModel],
    ) -> StructuredModel:
        del system_prompt
        payload = json.loads(user_prompt)
        job = payload.get("job", {})
        title = str(job.get("title", "Unknown role"))
        description = str(job.get("description", "")).lower()

        technical = 80 if any(term in description for term in ["c++", "simulation", "systems"]) else 55
        domain = 75 if any(term in description for term in ["aerospace", "spatial", "defense", "simulation"]) else 55
        role_interest = 75 if any(term in description for term in ["develop", "build", "engineering", "software"]) else 55
        fit = round((technical + domain + role_interest + 70) / 4)
        recommendation = "high" if fit >= 75 else "medium" if fit >= 60 else "low" if fit >= 40 else "skip"

        return response_model.model_validate(
            {
                "fit_score": fit,
                "technical_fit_score": technical,
                "domain_fit_score": domain,
                "role_interest_score": role_interest,
                "learning_potential_score": 70,
                "portfolio_alignment_score": 65,
                "summary": f"Mock evaluation for {title}.",
                "recommendation": recommendation,
                "reasoning": [
                    f"Mock evaluation for {title}.",
                    "Uses deterministic local scoring for development.",
                ],
                "risks": [
                    "Mock mode does not inspect the posting as deeply as a real model.",
                ],
                "suggested_positioning": None if recommendation == "skip" else [
                    "Emphasize relevant technical projects and learning velocity.",
                ],
            }
        )
