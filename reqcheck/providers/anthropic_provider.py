"""Anthropic Claude через официальный SDK ``anthropic``.

* Структурированный вывод: ``output_format`` (pydantic-схема) — ответ гарантированно JSON.
* Стриминг: длинные ответы с мышлением не упираются в HTTP-таймауты.
* Серверный fallback при отказе модели (beta ``server-side-fallback``) — включён
  по умолчанию, отключается ``ANTHROPIC_FALLBACKS=0``.
"""

from __future__ import annotations

import os

from pydantic import BaseModel

from .base import LLMError, LLMProvider, TransientLLMError

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str, effort: str = "high", max_tokens: int = 64000, **kw):
        super().__init__(model, **kw)
        import anthropic

        if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
            raise LLMError(
                "Не задан ANTHROPIC_API_KEY. Создайте ключ на https://platform.claude.com/ "
                "и укажите его в файле .env (см. .env.example)."
            )
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(max_retries=2)
        self.effort = effort
        self.max_tokens = max_tokens
        self.use_fallbacks = os.getenv("ANTHROPIC_FALLBACKS", "1") != "0"

    def _complete_json(self, system: str, user: str, schema: type[BaseModel]) -> str:
        a = self._anthropic
        kwargs: dict = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            output_config={"effort": self.effort},
        )
        if self.use_fallbacks:
            kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")
        try:
            with self._client.beta.messages.stream(**kwargs) as stream:
                msg = stream.get_final_message()
        except (a.RateLimitError, a.InternalServerError, a.APIConnectionError) as exc:
            raise TransientLLMError(f"Claude: {type(exc).__name__}: {exc}") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"Claude вернул ошибку {exc.status_code}: {exc.message}") from exc

        self.served_model = msg.model
        if msg.stop_reason == "refusal":
            raise LLMError("Claude отказался обрабатывать запрос (stop_reason=refusal).")
        if msg.stop_reason == "max_tokens":
            raise ValueError("Ответ обрезан по max_tokens — уменьшите BATCH_SIZE.")
        text = "".join(b.text for b in msg.content if b.type == "text")
        if not text:
            raise ValueError("Claude вернул ответ без текста")
        return text
