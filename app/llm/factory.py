from __future__ import annotations

import os
from typing import Literal

from dotenv import load_dotenv

from app.llm.base import LlmProvider
from app.llm.mock_client import MockLlmProvider
from app.llm.ollama_client import OllamaLlmProvider
from app.llm.openai_client import OpenAiLlmProvider


ProviderName = Literal["openai", "ollama", "mock"]


def configured_provider_name(provider: ProviderName | None = None) -> ProviderName:
    load_dotenv()
    selected = (provider or os.getenv("JOB_INTEL_LLM_PROVIDER") or "openai").lower()
    if selected not in {"openai", "ollama", "mock"}:
        raise RuntimeError(
            "Unsupported LLM provider. Use one of: openai, ollama, mock."
        )
    return selected  # type: ignore[return-value]


def create_llm_provider(provider: ProviderName | None = None) -> LlmProvider:
    selected = configured_provider_name(provider)
    if selected == "openai":
        return OpenAiLlmProvider.from_env()
    if selected == "ollama":
        return OllamaLlmProvider.from_env()
    return MockLlmProvider()
