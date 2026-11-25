"""Общий интерфейс провайдера LLM.

Агенту от модели нужно одно: получить по системному и пользовательскому
промпту JSON, который проходит валидацию pydantic-схемой. Всё остальное
(ретраи, кэш, разбор ошибок) прячется за этим интерфейсом.
"""

from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger("reqcheck")

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Ошибка обращения к модели, после которой продолжать прогон нельзя."""


class TransientLLMError(LLMError):
    """Временная ошибка (лимит запросов, перегрузка) — можно повторить."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class LLMProvider(ABC):
    name: str = "base"

    def __init__(self, model: str, max_retries: int = 5, temperature: float = 0.1):
        self.model = model
        self.max_retries = max_retries
        self.temperature = temperature
        self.calls = 0
        self.served_model: str | None = None  # какую модель реально вернул API

    # --- реализуют наследники -------------------------------------------------

    @abstractmethod
    def _complete_json(self, system: str, user: str, schema: type[BaseModel]) -> str:
        """Возвращает сырой JSON-текст ответа модели."""

    # --- общая логика ----------------------------------------------------------

    def generate(self, system: str, user: str, schema: type[T]) -> T:
        last_error: Exception | None = None
        prompt = user
        for attempt in range(1, self.max_retries + 1):
            try:
                self.calls += 1
                raw = self._complete_json(system, prompt, schema)
                return schema.model_validate(parse_json(raw))
            except TransientLLMError as exc:
                last_error = exc
                delay = exc.retry_after or min(60.0, 5.0 * 2 ** (attempt - 1))
                log.warning("Модель временно недоступна (%s). Повтор через %.0f с…", exc, delay)
                time.sleep(delay)
            except (ValidationError, ValueError) as exc:
                last_error = exc
                log.warning("Ответ модели не прошёл проверку схемы (попытка %d): %s", attempt, _short(exc))
                prompt = (
                    user
                    + "\n\nПредыдущий ответ не прошёл проверку JSON-схемы. Ошибка:\n"
                    + _short(exc, 1500)
                    + "\nВерни только корректный JSON строго по схеме."
                )
        raise LLMError(f"Не удалось получить корректный ответ модели: {last_error}")

    def label(self) -> str:
        return f"{self.name}:{self.served_model or self.model}"


def parse_json(raw: str):
    """Достаёт JSON из ответа, даже если модель обернула его в ```json … ```."""
    text = raw.strip()
    # Сначала — ответ целиком: внутри строк JSON могут быть ``` из цитат документа.
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        pass
    fence = re.match(r"^```(?:json)?\s*(.*)```$", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1).strip(), strict=False)
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1], strict=False)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Ответ модели не является корректным JSON: {exc}") from exc
    raise ValueError(f"Ответ модели не является JSON: {text[:200]!r}")


def inline_schema(schema: type[BaseModel]) -> dict:
    """JSON Schema без $ref/$defs — некоторые API понимают только плоские схемы."""
    full = schema.model_json_schema()
    defs = full.pop("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                return resolve(dict(defs[name]))
            return {
                k: resolve(v) for k, v in node.items() if not (k == "title" and isinstance(v, str))
            }
        if isinstance(node, list):
            return [resolve(x) for x in node]
        return node

    return resolve(full)


def _short(exc: Exception, limit: int = 400) -> str:
    s = str(exc)
    return s if len(s) <= limit else s[:limit] + "…"
