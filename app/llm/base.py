from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


class LlmProvider(Protocol):
    name: str
    model_name: str
    timeout_seconds: float | None

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[StructuredModel],
    ) -> StructuredModel:
        """Generate and validate a structured response."""
