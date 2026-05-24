from __future__ import annotations

import os

import requests
from dotenv import load_dotenv
from pydantic import ValidationError

from app.llm.base import StructuredModel


class OllamaLlmProvider:
    name = "ollama"

    def __init__(self, *, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model

    @classmethod
    def from_env(cls) -> "OllamaLlmProvider":
        load_dotenv()
        return cls(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=os.getenv("OLLAMA_MODEL", "qwen3"),
        )

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[StructuredModel],
    ) -> StructuredModel:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "format": response_model.model_json_schema(),
        }

        try:
            response = requests.post(
                f"{self._base_url}/api/chat",
                json=payload,
                timeout=120,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            raise RuntimeError(f"Ollama request failed: {error}") from error

        try:
            content = response.json().get("message", {}).get("content")
        except ValueError as error:
            raise RuntimeError(f"Ollama returned invalid JSON: {error}") from error
        if not content:
            raise RuntimeError("Ollama returned an empty response.")

        try:
            return response_model.model_validate_json(content)
        except ValidationError as error:
            raise RuntimeError(f"Ollama response did not match the schema: {error}") from error
