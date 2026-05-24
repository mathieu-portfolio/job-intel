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
        title_text = title.lower()

        risk = 70 if any(term in description for term in ["rockstar", "ninja", "fast-paced"]) else 25
        seniority = 35 if any(term in title_text for term in ["senior", "lead", "principal", "staff"]) else 75
        technical = 80 if any(term in description for term in ["c++", "simulation", "systems"]) else 55
        fit = round((technical + seniority + 70 + (100 - risk)) / 4)
        recommendation = "high" if fit >= 75 else "medium" if fit >= 60 else "low" if fit >= 40 else "skip"
        if seniority < 35:
            recommendation = "skip"

        return response_model.model_validate(
            {
                "fit_score": fit,
                "technical_fit_score": technical,
                "seniority_fit_score": seniority,
                "learning_potential_score": 70,
                "wording_risk_score": risk,
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
