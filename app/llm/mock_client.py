from __future__ import annotations

import json

from app.llm.base import StructuredModel


class MockLlmProvider:
    name = "mock"

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
        junior = 35 if any(term in title_text for term in ["senior", "lead", "principal", "staff"]) else 75
        technical = 80 if any(term in description for term in ["c++", "simulation", "systems"]) else 55
        fit = round((technical + junior + 70 + (100 - risk)) / 4)

        return response_model.model_validate(
            {
                "fit_score": fit,
                "technical_fit_score": technical,
                "junior_accessibility_score": junior,
                "learning_potential_score": 70,
                "wording_risk_score": risk,
                "portfolio_alignment_score": 65,
                "recommendation": "apply" if fit >= 70 else "consider",
                "reasoning": [
                    f"Mock evaluation for {title}.",
                    "Uses deterministic local scoring for development.",
                ],
                "risks": [
                    "Mock mode does not inspect the posting as deeply as a real model.",
                ],
                "suggested_positioning": [
                    "Emphasize relevant technical projects and learning velocity.",
                ],
            }
        )
