"""Оценка полноты отчёта агента по эталонному списку дефектов.

    python eval/score.py reports/report.json [eval/golden_defects.json]

Считает, какую долю эталонных дефектов агент нашёл (recall), с разбивкой по
критичности, и перечисляет пропущенные. Совпадение — по ключевым словам
в тексте дефекта (проблема + цитаты + рекомендация). Это грубая, но
воспроизводимая метрика; спорные случаи стоит проверить глазами.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def defect_text(d: dict) -> str:
    return " ".join([d.get("problem", ""), d.get("recommendation", ""), *d.get("quotes", [])]).lower()


def matches(text: str, groups: list[list[str]]) -> bool:
    return all(any(k.lower() in text for k in group) for group in groups)


def score(report: dict, golden: dict) -> dict:
    defects = report["defects"]
    texts = [(d["id"], defect_text(d)) for d in defects]
    found, missed = [], []
    for g in golden["items"]:
        hit = [did for did, t in texts if matches(t, g["match"])]
        (found if hit else missed).append({**g, "matched_by": hit})
    by_sev: dict[str, list[int]] = {}
    for g in found:
        by_sev.setdefault(g["severity"], [0, 0])[0] += 1
    for g in golden["items"]:
        by_sev.setdefault(g["severity"], [0, 0])[1] += 1
    return {
        "total_golden": len(golden["items"]),
        "found": found,
        "missed": missed,
        "recall": len(found) / max(1, len(golden["items"])),
        "by_severity": by_sev,
        "agent_defects": len(defects),
    }


def main(argv: list[str]) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    if not argv:
        print(__doc__)
        return 2
    report = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    golden_path = Path(argv[1]) if len(argv) > 1 else Path(__file__).with_name("golden_defects.json")
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    res = score(report, golden)

    print(f"Модель: {report['passport']['model']}  |  дефектов в отчёте: {res['agent_defects']}")
    print(f"Найдено эталонных дефектов: {len(res['found'])} из {res['total_golden']} "
          f"(recall {res['recall']:.0%})")
    for sev in ("critical", "major", "minor"):
        if sev in res["by_severity"]:
            f, t = res["by_severity"][sev]
            print(f"  {sev:<8} {f}/{t}")
    if res["missed"]:
        print("\nПропущены:")
        for g in res["missed"]:
            print(f"  {g['id']} [{g['severity']}] {g['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
