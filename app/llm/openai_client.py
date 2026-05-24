from __future__ import annotations

import os

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError
from pydantic import ValidationError

from app.llm.base import StructuredModel


class OpenAiLlmProvider:
    name = "openai"

    def __init__(self, *, api_key: str, model: str) -> None:
        self._client = OpenAI(api_key=api_key)
        self._model = model

    @classmethod
    def from_env(cls) -> "OpenAiLlmProvider":
        load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Copy .env.example to .env and set OPENAI_API_KEY, "
                "use `--provider mock`, or run `python -m app.cli rank --dry-run` to preview jobs "
                "without using an LLM."
            )
        return cls(api_key=api_key, model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[StructuredModel],
    ) -> StructuredModel:
        try:
            parsed_response = self._client.beta.chat.completions.parse(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=response_model,
            )
            parsed = parsed_response.choices[0].message.parsed
            if parsed is None:
                raise RuntimeError("OpenAI returned an empty structured response.")
            return parsed
        except AttributeError:
            return self._generate_structured_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=response_model,
            )
        except OpenAIError as error:
            raise RuntimeError(f"OpenAI request failed: {error}") from error
        except ValidationError as error:
            raise RuntimeError(f"OpenAI response did not match the schema: {error}") from error

    def _generate_structured_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[StructuredModel],
    ) -> StructuredModel:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )
        except OpenAIError as error:
            raise RuntimeError(f"OpenAI request failed: {error}") from error

        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("OpenAI returned an empty response.")
        try:
            return response_model.model_validate_json(content)
        except ValidationError as error:
            raise RuntimeError(f"OpenAI response did not match the schema: {error}") from error
