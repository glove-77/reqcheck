"""Общие фикстуры: поддельный провайдер LLM, чтобы тесты шли без ключа и сети."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reqcheck.models import (
    ExtractionResponse,
    RequirementBatchResponse,
    DedupResponse,
    SetResponse,
    VerdictResponse,
)
from reqcheck.providers.base import LLMProvider

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "examples" / "requirements.md"


class FakeProvider(LLMProvider):
    """Отвечает заготовками в зависимости от запрошенной схемы."""

    name = "fake"

    def __init__(self):
        super().__init__("fake-model", max_retries=2)
        self.requests: list[tuple[str, str]] = []

    def _complete_json(self, system, user, schema):
        self.requests.append((schema.__name__, user))
        if schema is ExtractionResponse:
            return json.dumps({
                "document_title": "Уведомление о расходах по карте",
                "product_context": "Мобильный банк: push при достижении месячного порога расходов по карте.",
                "requirements": [
                    {"doc_id": "Ф-1", "section": "Часть A / Функциональные требования",
                     "quote": "Держатель активной карты KZT задаёт целый порог от 1 000 до 1 000 000 ₸ включительно.",
                     "summary": "Порог от 1 000 до 1 000 000 ₸", "kind": "functional"},
                    {"doc_id": "Ф-2", "section": "Часть A / Функциональные требования",
                     "quote": "Месяц определяется по дате совершения покупки в Алматы.",
                     "summary": "Месяц по дате покупки", "kind": "functional"},
                    {"doc_id": "", "section": "Часть A / Нефункциональные требования",
                     "quote": "Сервис передаёт push на отправку в течение минуты после получения события.",
                     "summary": "Push в течение минуты", "kind": "nonfunctional"},
                    {"doc_id": "", "section": "Часть B / Общие параметры",
                     "quote": "Этой фразы в документе нет вообще",
                     "summary": "Выдуманное требование", "kind": "other"},
                ],
                "non_requirement_parts": ["Ревью требований", "Блок согласующих"],
            }, ensure_ascii=False)
        if schema is RequirementBatchResponse:
            ids = [line.split()[1] for line in user.split("<requirements_to_check>")[1].splitlines()
                   if line.startswith("### ")]
            assessments = []
            for rid in ids:
                defects = []
                if rid == "R-02":
                    defects.append({
                        "criterion": "COR", "severity": "critical",
                        "quotes": ["Месяц определяется по дате совершения покупки в Алматы",
                                   "Февральский счётчик вырос на 4 000 ₸"],
                        "problem": "Критерий приёмки №2 противоречит Ф-2: покупка 31 января учтена в феврале.",
                        "recommendation": "Исправить ожидаемый результат: январский счётчик вырос на 4 000 ₸.",
                    })
                assessments.append({"req_id": rid, "defects": defects, "comment": "Проверено."})
            return json.dumps({"assessments": assessments}, ensure_ascii=False)
        if schema is SetResponse:
            return json.dumps({
                "criteria": [{"criterion": "S-CON", "status": "violation", "summary": "Есть противоречия."}],
                "defects": [{
                    "criterion": "S-MOD", "severity": "minor", "related_requirements": [],
                    "quotes": ["Документ (версия/дата) в источнике не заполнен."],
                    "problem": "Нет версии и даты документа.",
                    "recommendation": "Добавить версию, дату и историю изменений.",
                }],
            }, ensure_ascii=False)
        if schema is DedupResponse:
            return json.dumps({"groups": []})
        if schema is VerdictResponse:
            return json.dumps({
                "ready_for_development": "yes", "ready_for_testing": "yes",
                "rationale": "Есть критическое противоречие D-001.",
                "priority_fixes": ["Устранить противоречие D-001."],
            }, ensure_ascii=False)
        raise AssertionError(f"неожиданная схема {schema}")


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def sample_path() -> Path:
    return SAMPLE
