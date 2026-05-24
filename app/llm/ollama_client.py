from __future__ import annotations

import os

import requests
from dotenv import load_dotenv
from pydantic import ValidationError

from app.llm.base import StructuredModel


def _ollama_timeout_from_env() -> float:
    raw_timeout = os.getenv("OLLAMA_TIMEOUT", "120")
    try:
        timeout = float(raw_timeout)
    except ValueError as error:
        raise RuntimeError("OLLAMA_TIMEOUT must be a number of seconds.") from error
    if timeout <= 0:
        raise RuntimeError("OLLAMA_TIMEOUT must be greater than zero.")
    return timeout


class OllamaLlmProvider:
    name = "ollama"

    def __init__(self, *, base_url: str, model: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls) -> "OllamaLlmProvider":
        load_dotenv()
        return cls(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=os.getenv("OLLAMA_MODEL", "qwen3"),
            timeout_seconds=_ollama_timeout_from_env(),
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    def check_ready(self) -> None:
        try:
            response = requests.get(
                f"{self._base_url}/api/tags",
                timeout=min(5, self._timeout_seconds),
            )
            response.raise_for_status()
        except requests.Timeout as error:
            raise RuntimeError(
                "Ollama health check timed out. Confirm `ollama serve` is running "
                f"and responding at {self._base_url}."
            ) from error
        except requests.RequestException as error:
            raise RuntimeError(
                "Ollama is not reachable. Start it with `ollama serve`, "
                f"then retry. Base URL: {self._base_url}. Details: {error}"
            ) from error

        try:
            payload = response.json()
        except ValueError as error:
            raise RuntimeError(f"Ollama health check returned invalid JSON: {error}") from error

        models = {str(model.get("name")) for model in payload.get("models", [])}
        model_aliases = set(models)
        model_aliases.update(name.removesuffix(":latest") for name in models)
        if self._model not in model_aliases:
            available = ", ".join(sorted(models)) or "none"
            raise RuntimeError(
                f"Ollama model `{self._model}` is not installed. "
                f"Run `ollama pull {self._model}` or set OLLAMA_MODEL. "
                f"Available models: {available}."
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
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
        except requests.Timeout as error:
            raise TimeoutError("Ollama request timed out.") from error
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
