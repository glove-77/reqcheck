"""Командная строка.

    python -m reqcheck <документ> <отчёт.md> [опции]

Рядом с отчётом сохраняется JSON с теми же данными (для интеграций и оценки качества).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from . import __version__
from .document import DocumentError, load_document
from .pipeline import Pipeline, PipelineError
from .providers import LLMError, create_provider
from .report import render_markdown

ROOT = Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="reqcheck",
        description="ИИ-агент статической проверки требований (ISO/IEC/IEEE 29148, IEEE 830).",
    )
    p.add_argument("input", help="Путь к документу с требованиями (.pdf, .docx, .md, .txt)")
    p.add_argument("output", help="Путь к файлу отчёта (.md). Рядом будет сохранён .json")
    p.add_argument("--provider", choices=["gemini", "anthropic", "openai"],
                   help="Провайдер LLM (по умолчанию LLM_PROVIDER из .env или по заданному ключу)")
    p.add_argument("--model", help="Модель (по умолчанию <PROVIDER>_MODEL из .env)")
    p.add_argument("--batch-size", type=int, default=None,
                   help="Сколько требований проверять за один запрос (по умолчанию 10)")
    p.add_argument("--no-cache", action="store_true", help="Не использовать кэш ответов модели")
    p.add_argument("--cache-dir", default=str(ROOT / ".cache" / "llm"), help="Папка кэша ответов модели")
    p.add_argument("--no-json", action="store_true", help="Не сохранять JSON рядом с отчётом")
    p.add_argument("-v", "--verbose", action="store_true", help="Подробный лог")
    p.add_argument("--version", action="version", version=f"reqcheck {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # кириллица в консоли Windows
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    args = build_parser().parse_args(argv)
    # Сторонние библиотеки — только предупреждения; свой лог — подробнее.
    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
    log = logging.getLogger("reqcheck")
    log.setLevel(logging.DEBUG if args.verbose else logging.INFO)

    load_dotenv(ROOT / ".env")
    load_dotenv()  # .env в текущей папке, если запуск не из корня проекта

    import os

    batch_size = args.batch_size or int(os.getenv("BATCH_SIZE", "10"))
    try:
        doc = load_document(args.input)
        log.info("Документ: %s (%s, %d символов)", doc.name, doc.format.upper(), len(doc.text))
        provider = create_provider(
            args.provider, args.model, cache_dir=None if args.no_cache else Path(args.cache_dir)
        )
        log.info("Модель: %s", provider.label())
        result = Pipeline(provider, batch_size=batch_size).run(doc)
    except (DocumentError, LLMError, PipelineError) as exc:
        log.error("Ошибка: %s", exc)
        return 1
    except KeyboardInterrupt:
        log.error("Прервано пользователем")
        return 130

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(result), encoding="utf-8")
    if not args.no_json:
        out.with_suffix(".json").write_text(result.model_dump_json(indent=2), encoding="utf-8")

    sev = {s: sum(1 for d in result.defects if d.severity == s) for s in ("critical", "major", "minor")}
    log.info(
        "Готово за %.0f с: требований %d, дефектов %d (критических %d, существенных %d, незначительных %d).",
        result.passport.duration_sec, len(result.requirements), len(result.defects),
        sev["critical"], sev["major"], sev["minor"],
    )
    log.info("Отчёт: %s", out.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
