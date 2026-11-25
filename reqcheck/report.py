"""Сборка отчёта в Markdown из результатов прогона.

Отчёт собирается кодом по шаблону, а не пишется моделью целиком: так все
обязательные разделы ТЗ присутствуют всегда, а цифры сводки считаются точно.
"""

from __future__ import annotations

from collections import Counter

from . import criteria
from .models import (
    READINESS_RU,
    SEVERITY_RU,
    STATUS_RU,
    Defect,
    RunResult,
)

SEV_ICON = {"critical": "🔴", "major": "🟠", "minor": "🟡"}
STATUS_ICON = {"ok": "✅", "partial": "⚠️", "violation": "❌"}
KIND_RU = {
    "functional": "функциональное", "nonfunctional": "нефункциональное", "data": "данные",
    "integration": "интеграция", "interface": "интерфейс/API", "ui": "UI",
    "acceptance": "критерий приёмки", "architecture": "архитектура", "process": "процесс",
    "other": "прочее",
}

# Группы критичных дефектов для сводки (как требует ТЗ хакатона)
CRITICAL_GROUPS = (
    ("Непроверяемые", {"VER", "S-VAL"}),
    ("Противоречивые и некорректные", {"COR", "S-CON", "S-FEA", "FEA"}),
    ("Неполные", {"CMP", "S-CMP"}),
    ("Неоднозначные", {"UNA", "S-CMH"}),
)


def render_markdown(r: RunResult) -> str:
    p = r.passport
    out: list[str] = []
    w = out.append
    by_req: dict[str, list[Defect]] = {}
    for d in r.defects:
        if d.scope == "requirement":
            for rid in d.requirement_ids:  # склеенный дефект виден у каждого затронутого требования
                by_req.setdefault(rid, []).append(d)
    set_defects = [d for d in r.defects if d.scope == "set"]
    sev = Counter(d.severity for d in r.defects)

    w(f"# Отчёт о проверке требований: {p.document_title}")
    w("")
    w(
        f"> **Итог:** разработка — **{READINESS_RU[r.verdict.ready_for_development]}**, "
        f"тестирование — **{READINESS_RU[r.verdict.ready_for_testing]}**. "
        f"Требований: {len(r.requirements)}, дефектов: {len(r.defects)} "
        f"(🔴 {sev['critical']} · 🟠 {sev['major']} · 🟡 {sev['minor']})."
    )
    w("")

    # 1. Паспорт ------------------------------------------------------------
    w("## 1. Паспорт прогона")
    w("")
    w("| Поле | Значение |")
    w("| :--- | :--- |")
    rows = [
        ("Документ", p.document_title),
        ("Файл", f"`{p.document_file}`"),
        ("SHA-256 файла", f"`{p.document_sha256[:16]}…`"),
        ("Дата и время прогона", p.run_datetime),
        ("Модель", f"{p.model} (провайдер: {p.provider})"),
        ("Версия агента", p.agent_version),
        ("Обращений к модели", str(p.llm_calls)),
        ("Длительность", f"{p.duration_sec} с"),
        ("Стандарты", "ISO/IEC/IEEE 29148:2018 п. 5.2.5, 5.2.6; IEEE Std 830-1998 п. 4.3"),
        ("Контекст (определён агентом)", r.product_context),
    ]
    for k, v in rows:
        w(f"| {k} | {_cell(v)} |")
    w("")

    # 2. Нарезка ------------------------------------------------------------
    w("## 2. Как агент нарезал требования")
    w("")
    w(
        "Идентификатор в отчёте `R-xx` присвоен агентом по порядку документа. Если в документе у "
        "требования был свой номер, он указан во втором столбце. Место — номер строки исходного "
        "текста" + (" и страница PDF" if any(q.location.page for q in r.requirements) else "") + "."
    )
    w("")
    w("| ID в отчёте | ID в документе | Раздел | Краткая формулировка | Цитата из документа | Место |")
    w("| :--- | :--- | :--- | :--- | :--- | :--- |")
    for q in r.requirements:
        place = q.location.render() if q.quote_verified else "цитата не найдена дословно"
        w(
            f"| {q.id} | {_cell(q.doc_id or '— (нет номера)')} | {_cell(q.section)} | "
            f"{_cell(q.summary)} | {_cell(_cut(q.quote, 300))} | {place} |"
        )
    if r.non_requirement_parts:
        w("")
        w("Не считались требованиями (служебные части документа, проверены на уровне набора): "
          + "; ".join(r.non_requirement_parts) + ".")
    w("")

    # 3. Проверка каждого требования ------------------------------------------
    w("## 3. Проверка каждого требования")
    w("")
    w("### 3.1. Матрица соответствия критериям")
    w("")
    w("✅ — нарушений не найдено; 🔴/🟠/🟡 — есть нарушение (цвет — самая высокая критичность).")
    w("")
    codes = criteria.INDIVIDUAL_CODES
    w("| Требование | " + " | ".join(codes) + " | Дефекты |")
    w("| :--- | " + " | ".join(":---:" for _ in codes) + " | :--- |")
    for q in r.requirements:
        ds = by_req.get(q.id, [])
        cells = []
        for c in codes:
            hit = [d for d in ds if d.criterion == c]
            cells.append(SEV_ICON[_worst(hit)] if hit else "✅")
        ids = ", ".join(d.id for d in ds) or "—"
        w(f"| {q.id} {_cell(q.doc_id)} | " + " | ".join(cells) + f" | {ids} |")
    w("")
    w("Расшифровка кодов: " + "; ".join(f"**{c.code}** — {c.name}" for c in criteria.INDIVIDUAL) + ".")
    w("")

    w("### 3.2. Замечания по требованиям")
    w("")
    for q in r.requirements:
        ds = by_req.get(q.id, [])
        title = f"{q.id}" + (f" · {q.doc_id}" if q.doc_id else "") + f" — {q.summary}"
        w(f"#### {title}")
        w("")
        w(f"*Раздел:* {q.section}. *Тип:* {KIND_RU.get(q.kind, q.kind)}. *Место:* "
          + (q.location.render() if q.quote_verified else "цитата не найдена дословно") + ".")
        w("")
        w("> " + _cut(q.quote, 700).replace("\n", "\n> "))
        w("")
        comment = r.requirement_comments.get(q.id, "")
        if not ds:
            w(f"✅ Нарушений не найдено. {comment}".rstrip())
            w("")
            continue
        if comment:
            w(f"**Вывод:** {comment}")
            w("")
        for d in ds:
            if d.requirement_ids[0] == q.id:
                _render_defect(w, d)
            else:
                w(f"- **{d.id}** {SEV_ICON[d.severity]} — тот же дефект, подробно описан у "
                  f"{d.requirement_ids[0]}: {_cut(_inline(d.problem), 160)}")
                w("")
    # 4. Набор ----------------------------------------------------------------
    w("## 4. Проверка всего набора требований")
    w("")
    w("| Критерий | Источник | Оценка | Обоснование |")
    w("| :--- | :--- | :--- | :--- |")
    for a in r.set_criteria:
        c = criteria.ALL[a.criterion]
        w(f"| **{c.code}** {c.name} | {c.source} | {STATUS_ICON[a.status]} {STATUS_RU[a.status]} | "
          f"{_cell(a.summary)} |")
    w("")
    if set_defects:
        w("### Дефекты уровня набора")
        w("")
        for d in set_defects:
            _render_defect(w, d)
    else:
        w("Дополнительных дефектов уровня набора не найдено.")
        w("")

    # 5. Сводка ---------------------------------------------------------------
    w("## 5. Сводка")
    w("")
    affected = {rid for d in r.defects if d.scope == "requirement" for rid in d.requirement_ids}
    w("| Показатель | Значение |")
    w("| :--- | ---: |")
    w(f"| Требований выделено | {len(r.requirements)} |")
    w(f"| Требований без замечаний | {len(r.requirements) - len(affected)} |")
    w(f"| Требований с замечаниями | {len(affected)} |")
    w(f"| Всего нарушений | {len(r.defects)} |")
    w(f"| 🔴 Критических | {sev['critical']} |")
    w(f"| 🟠 Существенных | {sev['major']} |")
    w(f"| 🟡 Незначительных | {sev['minor']} |")
    w(f"| Нарушений на уровне набора | {len(set_defects)} |")
    unverified = [d.id for d in r.defects if not d.quotes_verified]
    w(f"| Цитаты сверены с документом | {len(r.defects) - len(unverified)} из {len(r.defects)} |")
    w("")
    crit_counter = Counter(d.criterion for d in r.defects)
    if crit_counter:
        w("**Нарушения по критериям:** " + ", ".join(
            f"{criteria.ALL[k].name} ({k}) — {v}" for k, v in crit_counter.most_common()
        ) + ".")
        w("")

    w("### Критичные дефекты")
    w("")
    critical = [d for d in r.defects if d.severity == "critical"]
    if not critical:
        w("Критичных дефектов не найдено.")
    used: set[str] = set()
    for title, codes_set in CRITICAL_GROUPS:
        group = [d for d in critical if d.criterion in codes_set]
        if not group:
            continue
        w(f"**{title}:**")
        w("")
        for d in group:
            used.add(d.id)
            w(f"- {_defect_ref(d)}")
        w("")
    rest = [d for d in critical if d.id not in used]
    if rest:
        w("**Прочие критичные:**")
        w("")
        for d in rest:
            w(f"- {_defect_ref(d)}")
        w("")

    # 6. Итог -----------------------------------------------------------------
    v = r.verdict
    w("## 6. Итог")
    w("")
    w(f"- **Можно ли начинать разработку:** {READINESS_RU[v.ready_for_development]}")
    w(f"- **Можно ли начинать тестирование (проектирование тестов):** {READINESS_RU[v.ready_for_testing]}")
    w("")
    w(v.rationale)
    w("")
    w("**Что исправить в первую очередь:**")
    w("")
    for i, item in enumerate(v.priority_fixes, start=1):
        w(f"{i}. {item}")
    w("")

    # Приложения ------------------------------------------------------------
    w("---")
    w("")
    w("## Приложение A. Автоматические сигналы (без LLM)")
    w("")
    if r.lint:
        w("Найдены регулярными правилами; модель использовала их как подсказки и оценила по смыслу.")
        w("")
        w("| Правило | Что найдено | Место | Фрагмент |")
        w("| :--- | :--- | :--- | :--- |")
        for h in r.lint:
            w(f"| {h.rule} | {h.title} | {h.where()} | {_cell(h.fragment)} |")
    else:
        w("Автоматические правила ничего не нашли.")
    w("")
    w("## Приложение B. Методика")
    w("")
    w(
        "1. Документ прочитан и пронумерован по строкам; регулярные правила нашли формальные сигналы.\n"
        "2. Модель выделила требования с дословными цитатами (шаг 1).\n"
        "3. Каждое требование проверено по 10 критериям отдельного требования в контексте всего "
        "документа (шаг 2).\n"
        "4. Набор проверен целиком по 8 критериям набора; дубли с шагом 2 исключены (шаг 3).\n"
        "4а. Одна и та же проблема, найденная у нескольких требований, склеена в один дефект со "
        "ссылками на все затронутые требования.\n"
        "5. Каждая цитата дефекта автоматически сверена с текстом документа, место (строка) "
        "вычислено кодом, а не моделью. Несовпадения помечены.\n"
        "6. Сводка посчитана кодом; итог сформулирован моделью и проверен правилом: при критичных "
        "дефектах ответ «да» невозможен."
    )
    w("")
    w("**Критерии отдельного требования:**")
    w("")
    for c in criteria.INDIVIDUAL:
        w(f"- **{c.code}** {c.name} ({c.source}) — {c.definition}")
    w("")
    w("**Критерии набора требований:**")
    w("")
    for c in criteria.SET:
        w(f"- **{c.code}** {c.name} ({c.source}) — {c.definition}")
    w("")
    w(f"*Отчёт сформирован агентом reqcheck {p.agent_version}, модель {p.model}.*")
    w("")
    return "\n".join(out)


# ---------------------------------------------------------------------------

def _render_defect(w, d: Defect) -> None:
    c = criteria.ALL.get(d.criterion)
    cname = f"{c.name} ({c.code})" if c else d.criterion
    places = ", ".join(loc.render() for loc in d.locations) or "—"
    reqs = ", ".join(d.requirement_ids) if d.requirement_ids else "документ в целом"
    w(f"- **{d.id}** {SEV_ICON[d.severity]} {SEVERITY_RU[d.severity]} · {cname} · требования: {reqs} · место: {places}")
    for q in d.quotes:
        w(f"  - Цитата: «{_inline(q)}»")
    if not d.quotes_verified:
        w("  - ⚠️ Цитата не найдена в документе дословно — проверьте вручную.")
    w(f"  - **Проблема:** {_inline(d.problem)}")
    w(f"  - **Рекомендация:** {_inline(d.recommendation)}")
    w("")


def _defect_ref(d: Defect) -> str:
    c = criteria.ALL.get(d.criterion)
    reqs = ", ".join(d.requirement_ids) if d.requirement_ids else "набор"
    q = f" «{_cut(_inline(d.quotes[0]), 90)}»" if d.quotes else ""
    return f"**{d.id}** ({reqs}, {c.name if c else d.criterion}){q} — {_cut(_inline(d.problem), 200)}"


def _worst(ds: list[Defect]) -> str:
    order = ("critical", "major", "minor")
    return min((d.severity for d in ds), key=order.index)


def _cell(s: str) -> str:
    return " ".join(str(s).split()).replace("|", "\\|")


def _inline(s: str) -> str:
    return " ".join(str(s).split())


def _cut(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"
