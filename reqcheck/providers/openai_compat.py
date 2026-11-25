"""OpenAI и любые OpenAI-совместимые API (Ollama, vLLM, LM Studio, OpenRouter).

Для локальной модели в Ollama достаточно:
    LLM_PROVIDER=openai
    OPENAI_BASE_URL=http://localhost:11434/v1
    OPENAI_API_KEY=ollama
    OPENAI_MODEL=qwen2.5:14b
"""

from __future__ import annotations

import json
import os

from pydantic import BaseModel

from .base import LLMError, LLMProvider, TransientLLMError, inline_schema


class OpenAICompatProvider(LLMProvider):
    name = "openai"

    def __init__(self, model: str, **kw):
        super().__init__(model, **kw)
        try:
            import openai
        except ImportError as exc:  # пакет необязательный
            raise LLMError("Установите пакет openai: pip install openai") from exc

        key = os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL") or None
        if not key and not base_url:
            raise LLMError("Не задан OPENAI_API_KEY (или OPENAI_BASE_URL для локальной модели).")
        self._openai = openai
        self._local = bool(base_url)
        # Явный адрес по умолчанию: иначе SDK подхватит пустой OPENAI_BASE_URL= из .env и не подключится.
        self._client = openai.OpenAI(
            api_key=key or "local", base_url=base_url or "https://api.openai.com/v1", max_retries=2
        )

    def _complete_json(self, system: str, user: str, schema: type[BaseModel]) -> str:
        o = self._openai
        flat = inline_schema(schema)
        extra: dict = {}
        if self._local:
            # Локальные серверы: json_schema-режим поддерживают не все, поэтому схема — в промпте.
            system_full = (
                system + "\n\nОтвет — только JSON-объект по этой JSON Schema:\n"
                + json.dumps(flat, ensure_ascii=False)
            )
            response_format: dict = {"type": "json_object"}
            extra["temperature"] = self.temperature
        else:
            # OpenAI: строгий структурированный вывод — ответ гарантированно валиден по схеме.
            # (temperature не передаём: рассуждающие модели её не принимают.)
            system_full = system
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "strict": True, "schema": _strict(flat)},
            }
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                response_format=response_format,
                messages=[{"role": "system", "content": system_full}, {"role": "user", "content": user}],
                **extra,
            )
        except (o.RateLimitError, o.InternalServerError, o.APIConnectionError) as exc:
            raise TransientLLMError(f"OpenAI: {type(exc).__name__}: {exc}") from exc
        except o.APIStatusError as exc:
            raise LLMError(f"OpenAI-совместимый API вернул ошибку {exc.status_code}: {exc.message}") from exc
        self.served_model = resp.model
        choice = resp.choices[0] if resp.choices else None
        if choice is not None and choice.finish_reason == "length":
            raise ValueError("Ответ обрезан по длине — уменьшите BATCH_SIZE.")
        refusal = getattr(choice.message, "refusal", None) if choice else None
        if refusal:
            raise LLMError(f"Модель отказалась отвечать: {refusal}")
        text = choice.message.content if choice else None
        if not text:
            raise ValueError("Пустой ответ модели")
        return text


def _strict(node):
    """Строгий режим OpenAI: у каждого объекта additionalProperties=false и все поля обязательны."""
    if isinstance(node, dict):
        node = {k: _strict(v) for k, v in node.items()}
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"])
        node.pop("default", None)
        return node
    if isinstance(node, list):
        return [_strict(x) for x in node]
    return node
