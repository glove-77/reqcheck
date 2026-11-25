"""Google Gemini через официальный SDK google-genai (бесплатный ключ из Google AI Studio)."""

from __future__ import annotations

import os
import re

from pydantic import BaseModel

from .base import LLMError, LLMProvider, TransientLLMError, inline_schema


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, model: str, api_key: str | None = None, **kw):
        super().__init__(model, **kw)
        from google import genai

        key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise LLMError(
                "Не задан GEMINI_API_KEY. Получите бесплатный ключ на https://aistudio.google.com/ "
                "и укажите его в файле .env (см. .env.example)."
            )
        self._client = genai.Client(api_key=key)

    def _complete_json(self, system: str, user: str, schema: type[BaseModel]) -> str:
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self.temperature,
            response_mime_type="application/json",
            response_json_schema=inline_schema(schema),
        )
        try:
            resp = self._client.models.generate_content(model=self.model, contents=user, config=config)
        except errors.APIError as exc:
            code = getattr(exc, "code", None)
            if code in (429, 500, 502, 503, 504):
                raise TransientLLMError(f"Gemini {code}: {exc.message}", _retry_after(str(exc))) from exc
            raise LLMError(f"Gemini вернул ошибку {code}: {exc.message}") from exc

        self.served_model = getattr(resp, "model_version", None) or self.model
        text = resp.text
        if not text:
            reason = resp.candidates[0].finish_reason if resp.candidates else None
            raise ValueError(f"Gemini вернул пустой ответ (finish_reason={reason})")
        return text


def _retry_after(message: str) -> float | None:
    m = re.search(r"retry in ([\d.]+)s", message, re.IGNORECASE) or re.search(
        r"retryDelay['\"]?:\s*['\"]?(\d+)", message
    )
    return float(m.group(1)) + 1 if m else None
