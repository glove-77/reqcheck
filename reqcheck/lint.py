"""Детерминированные проверки без LLM.

Ищут формальные признаки дефектов: размытые слова, заглушки, пустые поля,
незаполненные чек-листы. Результаты:
* передаются модели как подсказки (модель решает, дефект ли это в контексте);
* выводятся в приложении отчёта как воспроизводимые автоматические сигналы.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .document import PAGE_BREAK, Document
from .models import LintHit


@dataclass(frozen=True)
class Rule:
    code: str
    title: str
    pattern: re.Pattern[str]


def _words(*items: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w-])(" + "|".join(items) + r")", re.IGNORECASE)


RULES: tuple[Rule, ...] = (
    Rule(
        "L-VAGUE", "Размытая, неизмеримая формулировка",
        _words(
            r"быстр\w*", r"удобн\w*", r"интуитивн\w*", r"оптимальн\w*", r"эффективн\w*",
            r"корректн\w*", r"своевременн\w*", r"надёжн\w*", r"надежн\w*", r"гибк\w*",
            r"достаточн\w*", r"максимально", r"минимально", r"по возможности",
            r"при необходимости", r"как правило", r"обычно", r"user-friendly",
            r"современн\w*", r"простой", r"просто",
        ),
    ),
    Rule(
        "L-OPEN", "Открытый перечень (и т.п., и др.)",
        re.compile(r"(и\s+т\.\s?[пд]\.?|и\s+др\.|и\s+тому\s+подобное|etc\.?)", re.IGNORECASE),
    ),
    Rule(
        "L-TBD", "Заглушка или отложенное решение",
        re.compile(
            r"(TBD|TODO|уточнить|будет\s+определ\w*|на\s+усмотрение|заполня\w+\s+\w+|не\s+заполнен\w*)",
            re.IGNORECASE,
        ),
    ),
    Rule(
        "L-EMPTY", "Пустое значение поля («—»)",
        re.compile(r"(:\s*[—–-]\s*\.?\s*$|\|\s*[—–-]\s*\|)", re.MULTILINE),
    ),
    Rule(
        "L-CHECKBOX", "Не заполнен вариант выбора («Да / Нет»)",
        re.compile(r"Да\s*/\s*Нет", re.IGNORECASE),
    ),
    Rule(
        "L-EMPTYROW", "Пустая строка таблицы",
        re.compile(r"^\|(\s*\|){2,}\s*$", re.MULTILINE),
    ),
    Rule(
        "L-PASSIVE", "Не указан исполнитель действия",
        _words(r"должно\s+быть\s+обеспечено", r"предусматривается", r"осуществляется"),
    ),
)

MAX_HITS_PER_RULE = 15


def run_lint(doc: Document) -> list[LintHit]:
    hits: list[LintHit] = []
    per_rule: dict[str, int] = {}
    for page_no, page in enumerate(doc.text.split(PAGE_BREAK), start=1):
        for line_no, line in enumerate(page.splitlines(), start=1):
            if not line.strip():
                continue
            for rule in RULES:
                if per_rule.get(rule.code, 0) >= MAX_HITS_PER_RULE:
                    continue
                if rule.pattern.search(line):
                    prev = hits[-1] if hits else None
                    if (
                        prev is not None
                        and prev.rule == rule.code
                        and prev.page == (page_no if doc.has_pages else None)
                        and (prev.line_end or prev.line) == line_no - 1
                    ):
                        prev.line_end = line_no  # склеиваем подряд идущие строки (таблицы)
                        continue
                    per_rule[rule.code] = per_rule.get(rule.code, 0) + 1
                    hits.append(
                        LintHit(
                            rule=rule.code,
                            title=rule.title,
                            line=line_no,
                            page=page_no if doc.has_pages else None,
                            fragment=_shorten(line.strip()),
                        )
                    )
    return hits


def _shorten(s: str, limit: int = 160) -> str:
    return s if len(s) <= limit else s[: limit - 1] + "…"


def render_for_prompt(hits: list[LintHit]) -> str:
    if not hits:
        return "Автоматические проверки ничего не нашли."
    rows = []
    for h in hits:
        rows.append(f"- [{h.rule}] {h.title} — {h.where()}: {h.fragment}")
    return "\n".join(rows)
