"""Кэш ответов модели на диске.

Ключ — хэш (провайдер, модель, системный промпт, запрос, схема). Даёт:
* повторный прогон без новых запросов (бесплатный тариф Gemini ограничен по RPD);
* воспроизводимость: тот же документ и та же версия агента дают тот же отчёт.
Отключается флагом ``--no-cache``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel

from .base import LLMProvider


class CachingProvider(LLMProvider):
    def __init__(self, inner: LLMProvider, cache_dir: Path):
        super().__init__(inner.model, max_retries=1)
        self.inner = inner
        self.name = inner.name
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.hits = 0

    def _key(self, system: str, user: str, schema: type[BaseModel]) -> str:
        h = hashlib.sha256()
        for part in (self.inner.name, self.inner.model, system, user, json.dumps(schema.model_json_schema())):
            h.update(part.encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()[:32]

    def generate(self, system, user, schema):
        path = self.cache_dir / f"{self._key(system, user, schema)}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.hits += 1
            self.served_model = data.get("served_model") or self.served_model
            return schema.model_validate(data["response"])
        result = self.inner.generate(system, user, schema)
        self.served_model = self.inner.served_model
        path.write_text(
            json.dumps(
                {"served_model": self.served_model, "response": result.model_dump()},
                ensure_ascii=False, indent=1,
            ),
            encoding="utf-8",
        )
        return result

    def _complete_json(self, system, user, schema):  # pragma: no cover - не используется
        raise NotImplementedError

    @property
    def calls(self) -> int:  # type: ignore[override]
        return self.inner.calls

    @calls.setter
    def calls(self, value: int) -> None:
        pass
