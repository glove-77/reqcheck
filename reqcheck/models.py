"""Модели данных.

Две группы:
* схемы ответов модели (``*Response``) — то, что LLM обязана вернуть в JSON;
* доменные объекты (``Defect``, ``RunResult`` …) — то, из чего собирается отчёт.

Схемы ответов держим простыми (строки, списки, Literal): так их одинаково
понимают структурированные выводы Claude, Gemini и OpenAI-совместимых моделей.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "major", "minor"]
Status = Literal["ok", "partial", "violation"]
Readiness = Literal["yes", "conditional", "no"]

IndividualCode = Literal["NEC", "APP", "UNA", "CMP", "SIN", "FEA", "VER", "COR", "CNF", "TRC"]
SetCode = Literal["S-CMP", "S-CON", "S-FEA", "S-CMH", "S-VAL", "S-MOD", "S-RNK", "S-TRC"]

SEVERITY_RU = {"critical": "Критический", "major": "Существенный", "minor": "Незначительный"}
STATUS_RU = {"ok": "соответствует", "partial": "частично", "violation": "нарушено"}
READINESS_RU = {"yes": "Да", "conditional": "Условно (с оговорками)", "no": "Нет"}


# --------------------------------------------------------------------------
# Шаг 1. Нарезка требований
# --------------------------------------------------------------------------

class ExtractedRequirement(BaseModel):
    doc_id: str = Field(
        description="Идентификатор в документе (например «Ф-1»). Если номера нет — пустая строка."
    )
    section: str = Field(description="Раздел документа, где находится требование (путь заголовков).")
    quote: str = Field(description="Дословная цитата требования из документа, без изменений.")
    summary: str = Field(description="Краткая формулировка требования, до 15 слов.")
    kind: Literal[
        "functional", "nonfunctional", "data", "integration", "interface",
        "ui", "acceptance", "architecture", "process", "other",
    ]


class ExtractionResponse(BaseModel):
    document_title: str
    product_context: str = Field(description="1–2 предложения: о чём документ и для какой системы.")
    requirements: list[ExtractedRequirement]
    non_requirement_parts: list[str] = Field(
        description="Разделы, которые не являются требованиями (шаблоны, служебные таблицы)."
    )


# --------------------------------------------------------------------------
# Шаг 2. Проверка каждого требования
# --------------------------------------------------------------------------

class LLMDefect(BaseModel):
    criterion: IndividualCode
    severity: Severity
    quotes: list[str] = Field(description="Дословные фрагменты документа, на которых основан дефект.")
    problem: str = Field(description="Что именно не так и к чему это приведёт. Конкретно, со ссылкой на текст.")
    recommendation: str = Field(description="Готовая исправленная формулировка или конкретное действие.")


class RequirementAssessment(BaseModel):
    req_id: str = Field(description="Идентификатор требования в отчёте (R-01 …).")
    defects: list[LLMDefect]
    comment: str = Field(description="Короткий вывод по требованию (1 предложение).")


class RequirementBatchResponse(BaseModel):
    assessments: list[RequirementAssessment]


# --------------------------------------------------------------------------
# Шаг 3. Проверка набора целиком
# --------------------------------------------------------------------------

class SetCriterionAssessment(BaseModel):
    criterion: SetCode
    status: Status
    summary: str


class LLMSetDefect(BaseModel):
    criterion: SetCode
    severity: Severity
    related_requirements: list[str] = Field(description="ID требований (R-xx), которых касается дефект.")
    quotes: list[str]
    problem: str
    recommendation: str


class SetResponse(BaseModel):
    criteria: list[SetCriterionAssessment]
    defects: list[LLMSetDefect]


# --------------------------------------------------------------------------
# Склейка дублей
# --------------------------------------------------------------------------

class DuplicateGroup(BaseModel):
    defect_ids: list[str] = Field(description="ID дефектов (T-xxx), описывающих одну и ту же проблему.")
    reason: str = Field(description="Почему это одна проблема (одно предложение).")


class DedupResponse(BaseModel):
    groups: list[DuplicateGroup] = Field(description="Только группы из двух и более дефектов.")


# --------------------------------------------------------------------------
# Шаг 4. Итог
# --------------------------------------------------------------------------

class VerdictResponse(BaseModel):
    ready_for_development: Readiness
    ready_for_testing: Readiness
    rationale: str = Field(description="2–4 предложения с опорой на конкретные дефекты (D-xxx).")
    priority_fixes: list[str] = Field(
        description="Упорядоченный список: что исправить в первую очередь, со ссылками на D-xxx."
    )


# --------------------------------------------------------------------------
# Доменные объекты
# --------------------------------------------------------------------------

class Location(BaseModel):
    page: int | None = None
    line: int | None = None

    def render(self) -> str:
        parts = []
        if self.page is not None:
            parts.append(f"стр. {self.page}")
        if self.line is not None:
            parts.append(f"строка {self.line}")
        return ", ".join(parts) or "—"


class Requirement(BaseModel):
    id: str
    doc_id: str
    section: str
    quote: str
    summary: str
    kind: str
    quote_verified: bool = False
    location: Location = Field(default_factory=Location)


class Defect(BaseModel):
    id: str = ""
    scope: Literal["requirement", "set"]
    criterion: str
    severity: Severity
    requirement_ids: list[str]
    quotes: list[str]
    problem: str
    recommendation: str
    quotes_verified: bool = False
    locations: list[Location] = Field(default_factory=list)


class LintHit(BaseModel):
    rule: str
    title: str
    line: int
    line_end: int | None = None  # если правило сработало на нескольких строках подряд
    page: int | None = None
    fragment: str

    def where(self) -> str:
        lines = f"строки {self.line}–{self.line_end}" if self.line_end else f"строка {self.line}"
        return f"стр. {self.page}, {lines}" if self.page else lines


class RunPassport(BaseModel):
    document_title: str
    document_file: str
    document_sha256: str
    run_datetime: str
    provider: str
    model: str
    agent_version: str
    duration_sec: float = 0.0
    llm_calls: int = 0
    cached_responses: int = 0  # ответы, взятые из кэша прошлых прогонов


class RunResult(BaseModel):
    passport: RunPassport
    product_context: str
    requirements: list[Requirement]
    non_requirement_parts: list[str]
    requirement_comments: dict[str, str]
    defects: list[Defect]
    set_criteria: list[SetCriterionAssessment]
    lint: list[LintHit]
    verdict: VerdictResponse
