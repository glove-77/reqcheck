"""Фабрика провайдеров LLM."""

from __future__ import annotations

import os
from pathlib import Path

from .base import LLMError, LLMProvider, TransientLLMError
from .cache import CachingProvider

DEFAULT_MODELS = {
    "gemini": "gemini-3.8-flash",
    "anthropic": "claude-opus-5-5",
    "openai": "gpt-6.2-sol",
}

__all__ = ["LLMError", "LLMProvider", "TransientLLMError", "create_provider", "detect_provider"]


def detect_provider() -> str:
    """Провайдер из LLM_PROVIDER, иначе — по тому, какой ключ задан."""
    explicit = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if explicit:
        return explicit
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        return "gemini"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_BASE_URL"):
        return "openai"
    return "gemini"


def create_provider(
    provider: str | None = None,
    model: str | None = None,
    cache_dir: Path | None = None,
) -> LLMProvider:
    name = (provider or detect_provider()).lower()
    env_model = os.getenv(f"{name.upper()}_MODEL")
    model = model or env_model or DEFAULT_MODELS.get(name)

    if name == "gemini":
        from .gemini import GeminiProvider

        inner: LLMProvider = GeminiProvider(model)
    elif name in ("anthropic", "claude"):
        from .anthropic_provider import AnthropicProvider

        inner = AnthropicProvider(model, effort=os.getenv("ANTHROPIC_EFFORT", "high"))
    elif name == "openai":
        from .openai_compat import OpenAICompatProvider

        inner = OpenAICompatProvider(model)
    else:
        raise LLMError(f"Неизвестный провайдер «{name}». Доступны: gemini, anthropic, openai.")

    return CachingProvider(inner, cache_dir) if cache_dir else inner
