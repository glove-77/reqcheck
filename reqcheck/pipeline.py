"""Конвейер проверки: документ → требования → проверка каждого → проверка набора → итог.

Каждый шаг — отдельный вызов модели со строгой JSON-схемой. Между шагами код:
* присваивает идентификаторы и сверяет цитаты с документом (защита от галлюцинаций);
* добирает требования, которые модель пропустила;
* удаляет дубли и считает статистику детерминированно, без модели.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime
from typing import Callable, Iterable

from . import criteria
from .document import Document, normalize
from .lint import render_for_prompt, run_lint
from .models import (
    DedupResponse,
    Defect,
    ExtractionResponse,
    Location,
    Requirement,
    RequirementBatchResponse,
    RunPassport,
    RunResult,
    SetCriterionAssessment,
    SetResponse,
    VerdictResponse,
)
from .prompts import (
    SYSTEM,
    dedup_prompt,
    extraction_prompt,
    requirements_check_prompt,
    set_check_prompt,
    verdict_prompt,
)
from .providers import LLMProvider
from .version import agent_version

log = logging.getLogger("reqcheck")

SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2}


class PipelineError(Exception):
    pass


class Pipeline:
    def __init__(
        self,
        provider: LLMProvider,
        batch_size: int = 10,
        on_step: Callable[[str], None] | None = None,
    ):
        self.llm = provider
        self.batch_size = max(1, batch_size)
        self.on_step = on_step or (lambda msg: log.info(msg))

    # ------------------------------------------------------------------
    def run(self, doc: Document) -> RunResult:
        started = time.monotonic()
        run_dt = datetime.now().astimezone()

        self.on_step("Автоматические проверки текста (без LLM)…")
        lint = run_lint(doc)
        lint_text = render_for_prompt(lint)
        numbered = doc.numbered_text()

        # --- 1. Нарезка ---------------------------------------------------
        self.on_step("Шаг 1/4. Нарезка документа на требования…")
        extraction = self.llm.generate(SYSTEM, extraction_prompt(numbered), ExtractionResponse)
        requirements = self._build_requirements(doc, extraction)
        if not requirements:
            raise PipelineError("Модель не выделила ни одного требования. Проверьте, что документ прочитан.")
        self.on_step(f"   выделено требований: {len(requirements)}")
        context = extraction.product_context.strip() or "не определён"
        catalogue = "\n".join(
            f"- {r.id} [{r.doc_id or 'без номера'}] {r.section}: {r.summary}" for r in requirements
        )

        # --- 2. Проверка каждого требования ------------------------------
        comments: dict[str, str] = {}
        defects: list[Defect] = []
        batches = list(_chunks(requirements, self.batch_size))
        for i, batch in enumerate(batches, start=1):
            self.on_step(f"Шаг 2/4. Проверка требований, пакет {i}/{len(batches)} ({batch[0].id}–{batch[-1].id})…")
            self._check_batch(batch, numbered, context, catalogue, lint_text, comments, defects)

        # --- 3. Проверка набора ------------------------------------------
        self.on_step("Шаг 3/4. Проверка набора требований целиком…")
        found = "\n".join(
            f"- {d.requirement_ids[0]} {d.criterion} ({d.severity}): {_cut(d.problem, 180)}" for d in defects
        ) or "нет"
        non_req = "\n".join(f"- {p}" for p in extraction.non_requirement_parts) or "нет"
        set_resp = self.llm.generate(
            SYSTEM,
            set_check_prompt(numbered, context, catalogue, found, lint_text, non_req),
            SetResponse,
        )
        known_ids = {r.id for r in requirements}
        for d in set_resp.defects:
            defects.append(
                Defect(
                    scope="set",
                    criterion=d.criterion,
                    severity=d.severity,
                    requirement_ids=[x for x in d.related_requirements if x in known_ids],
                    quotes=[q for q in d.quotes if q.strip()],
                    problem=d.problem.strip(),
                    recommendation=d.recommendation.strip(),
                )
            )
        set_criteria = _complete_set_criteria(set_resp.criteria)

        # --- Сверка цитат, дедупликация, нумерация -----------------------
        defects = _dedupe(defects)
        if len(defects) > 1:
            self.on_step("Склейка дублей…")
            defects = self._merge_duplicates(defects, {r.id: i for i, r in enumerate(requirements)})
        order = {r.id: i for i, r in enumerate(requirements)}
        defects.sort(
            key=lambda d: (
                0 if d.scope == "requirement" else 1,
                order.get(d.requirement_ids[0], 10_000) if d.requirement_ids else 10_000,
                SEVERITY_ORDER[d.severity],
            )
        )
        for n, d in enumerate(defects, start=1):
            d.id = f"D-{n:03d}"
            locs = [doc.locate(q) for q in d.quotes]
            d.locations = [loc for loc in locs if loc is not None]
            d.quotes_verified = bool(d.quotes) and all(loc is not None for loc in locs)
        unverified = sum(1 for d in defects if not d.quotes_verified)
        if unverified:
            log.info("Дефектов с цитатой, не найденной дословно: %d", unverified)

        # --- 4. Итог ------------------------------------------------------
        self.on_step("Шаг 4/4. Итоговое заключение…")
        verdict = self.llm.generate(
            SYSTEM,
            verdict_prompt(context, _stats_text(requirements, defects), _defects_brief(defects)),
            VerdictResponse,
        )
        verdict = _enforce_verdict(verdict, defects)

        passport = RunPassport(
            document_title=extraction.document_title.strip() or doc.title,
            document_file=doc.name,
            document_sha256=doc.sha256,
            run_datetime=_format_dt(run_dt),
            provider=self.llm.name,
            model=self.llm.served_model or self.llm.model,
            agent_version=agent_version(),
            duration_sec=round(time.monotonic() - started, 1),
            llm_calls=self.llm.calls,
            cached_responses=getattr(self.llm, "hits", 0),
        )
        return RunResult(
            passport=passport,
            product_context=context,
            requirements=requirements,
            non_requirement_parts=extraction.non_requirement_parts,
            requirement_comments=comments,
            defects=defects,
            set_criteria=set_criteria,
            lint=lint,
            verdict=verdict,
        )

    # ------------------------------------------------------------------
    def _build_requirements(self, doc: Document, ex: ExtractionResponse) -> list[Requirement]:
        result: list[Requirement] = []
        seen: set[str] = set()
        for item in ex.requirements:
            key = normalize(item.quote)
            if not key or key in seen:
                continue
            seen.add(key)
            loc = doc.locate(item.quote)
            result.append(
                Requirement(
                    id=f"R-{len(result) + 1:02d}",
                    doc_id=item.doc_id.strip(),
                    section=item.section.strip(),
                    quote=item.quote.strip(),
                    summary=item.summary.strip(),
                    kind=item.kind,
                    quote_verified=loc is not None,
                    location=loc or Location(),
                )
            )
        missing = [r.id for r in result if not r.quote_verified]
        if missing:
            log.info("Цитаты требований, не найденные дословно: %s", ", ".join(missing))
        return result

    def _merge_duplicates(self, defects: list[Defect], order: dict[str, int]) -> list[Defect]:
        """Одна проблема, найденная у нескольких требований, становится одним дефектом."""
        tmp = {f"T-{i:03d}": d for i, d in enumerate(defects, start=1)}
        listing = "\n".join(
            f"- {tid} [{d.criterion}, {d.severity}] "
            f"{', '.join(d.requirement_ids) or 'набор'}: {_cut(d.problem, 260)}"
            for tid, d in tmp.items()
        )
        resp = self.llm.generate(SYSTEM, dedup_prompt(listing), DedupResponse)

        absorbed: set[str] = set()
        for group in resp.groups:
            ids = [t for t in dict.fromkeys(group.defect_ids) if t in tmp and t not in absorbed]
            if len(ids) < 2:
                continue
            members = [tmp[t] for t in ids]
            # Основной — самый критичный, при равенстве — у более раннего требования.
            primary = min(
                members,
                key=lambda d: (
                    SEVERITY_ORDER[d.severity],
                    min((order.get(r, 10_000) for r in d.requirement_ids), default=10_000),
                ),
            )
            reqs = sorted(
                {r for d in members for r in d.requirement_ids}, key=lambda r: order.get(r, 10_000)
            )
            if primary.requirement_ids:  # требование основного дефекта — первым
                reqs.remove(primary.requirement_ids[0])
                reqs.insert(0, primary.requirement_ids[0])
            quotes: list[str] = []
            seen_q: set[str] = set()
            for d in [primary, *[m for m in members if m is not primary]]:
                for q in d.quotes:
                    if normalize(q) not in seen_q and len(quotes) < 4:
                        seen_q.add(normalize(q))
                        quotes.append(q)
            primary.requirement_ids = reqs
            primary.quotes = quotes
            absorbed.update(t for t in ids if tmp[t] is not primary)
        merged = [d for t, d in tmp.items() if t not in absorbed]
        if absorbed:
            log.info("Склеено дублей: %d → %d дефектов", len(defects), len(merged))
        return merged

    def _check_batch(self, batch, numbered, context, catalogue, lint_text, comments, defects) -> None:
        pending = list(batch)
        for attempt in range(2):  # вторая попытка — только для требований, которые модель пропустила
            if not pending:
                return
            text = "\n\n".join(
                f"### {r.id} (в документе: {r.doc_id or 'без номера'}; раздел: {r.section})\n{r.quote}"
                for r in pending
            )
            resp = self.llm.generate(
                SYSTEM,
                requirements_check_prompt(numbered, context, text, catalogue, lint_text),
                RequirementBatchResponse,
            )
            ids = {r.id for r in pending}
            for a in resp.assessments:
                rid = a.req_id.strip()
                if rid not in ids or rid in comments:
                    continue
                comments[rid] = a.comment.strip()
                for d in a.defects:
                    defects.append(
                        Defect(
                            scope="requirement",
                            criterion=d.criterion,
                            severity=d.severity,
                            requirement_ids=[rid],
                            quotes=[q for q in d.quotes if q.strip()],
                            problem=d.problem.strip(),
                            recommendation=d.recommendation.strip(),
                        )
                    )
            pending = [r for r in pending if r.id not in comments]
            if pending and attempt == 0:
                log.warning("Модель не вернула оценку для %s — повторяю.", ", ".join(r.id for r in pending))
        for r in pending:
            comments[r.id] = "Модель не вернула оценку этого требования."


# ----------------------------------------------------------------------
# Вспомогательные функции
# ----------------------------------------------------------------------

def _format_dt(dt: datetime) -> str:
    off = dt.strftime("%z")  # +0500
    tz = f"UTC{off[:3]}:{off[3:]}" if off else "UTC"
    return f"{dt:%Y-%m-%d %H:%M:%S} ({tz})"


def _chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _cut(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _dedupe(defects: list[Defect]) -> list[Defect]:
    seen: set[tuple] = set()
    out = []
    for d in defects:
        key = (
            d.criterion,
            tuple(sorted(d.requirement_ids)),
            tuple(sorted(normalize(q) for q in d.quotes)),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def _complete_set_criteria(items: list[SetCriterionAssessment]) -> list[SetCriterionAssessment]:
    by_code = {i.criterion: i for i in items}
    result = []
    for code in criteria.SET_CODES:
        result.append(
            by_code.get(code)
            or SetCriterionAssessment(criterion=code, status="partial", summary="Модель не вернула оценку.")
        )
    return result


def _stats_text(reqs: list[Requirement], defects: list[Defect]) -> str:
    sev = Counter(d.severity for d in defects)
    crit = Counter(d.criterion for d in defects)
    affected = {rid for d in defects if d.scope == "requirement" for rid in d.requirement_ids}
    return (
        f"Требований: {len(reqs)}; требований с дефектами: {len(affected)}.\n"
        f"Дефектов: {len(defects)} (critical {sev['critical']}, major {sev['major']}, minor {sev['minor']}).\n"
        f"По критериям: " + ", ".join(f"{k} — {v}" for k, v in crit.most_common())
    )


def _defects_brief(defects: list[Defect]) -> str:
    return "\n".join(
        f"- {d.id} [{d.severity}] {d.criterion} "
        f"{('(' + ', '.join(d.requirement_ids) + ')') if d.requirement_ids else '(набор)'}: {_cut(d.problem, 220)}"
        for d in defects
    ) or "нет"


def _enforce_verdict(v: VerdictResponse, defects: list[Defect]) -> VerdictResponse:
    """Страховка от слишком оптимистичного итога: при критических дефектах «да» невозможно."""
    if any(d.severity == "critical" for d in defects):
        if v.ready_for_development == "yes":
            v.ready_for_development = "conditional"
        if v.ready_for_testing == "yes":
            v.ready_for_testing = "conditional"
    return v
