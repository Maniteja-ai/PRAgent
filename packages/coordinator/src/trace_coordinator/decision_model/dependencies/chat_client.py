"""Construct a configured LangChain chat provider client."""

import os
from typing import Any

from trace_coordinator.config import GeminiProvider, OpenAIProvider


def create_chat_model(config: GeminiProvider | OpenAIProvider) -> Any:
    key = os.environ.get(config.api_key_env)
    if not key:
        raise ValueError(f"Set environment variable {config.api_key_env}")
    options: dict[str, Any] = {
        "model": config.model,
        "api_key": key,
        "temperature": 0,
        "timeout": config.timeout_seconds,
        "max_output_tokens": config.max_output_tokens,
    }
    if config.provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(**options, max_retries=1, vertexai=False)
    from langchain_openai import ChatOpenAI

    options["max_tokens"] = options.pop("max_output_tokens")
    return ChatOpenAI(**options, max_retries=0)
