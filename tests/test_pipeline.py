import json

from reqcheck import cli
from reqcheck.document import load_document
from reqcheck.lint import run_lint
from reqcheck.pipeline import Pipeline
from reqcheck.providers.base import inline_schema, parse_json
from reqcheck.models import RequirementBatchResponse
from reqcheck.report import render_markdown


def test_lint_finds_placeholders(sample_path):
    hits = run_lint(load_document(sample_path))
    rules = {h.rule for h in hits}
    assert {"L-TBD", "L-CHECKBOX", "L-EMPTY", "L-EMPTYROW"} <= rules
    tbd = [h for h in hits if h.rule == "L-TBD"]
    assert any("заполняются тестировщиками" in h.fragment for h in tbd)


def test_pipeline_end_to_end(sample_path, fake_provider):
    doc = load_document(sample_path)
    result = Pipeline(fake_provider, batch_size=2).run(doc)

    # нарезка: ID присвоены по порядку, выдуманная цитата помечена
    assert [r.id for r in result.requirements] == ["R-01", "R-02", "R-03", "R-04"]
    assert result.requirements[0].quote_verified
    assert not result.requirements[3].quote_verified

    # дефекты: пронумерованы, цитаты сверены, место вычислено кодом
    assert [d.id for d in result.defects] == ["D-001", "D-002"]
    d1 = result.defects[0]
    assert d1.scope == "requirement" and d1.requirement_ids == ["R-02"]
    assert d1.quotes_verified and len(d1.locations) == 2

    # все 8 критериев набора присутствуют, даже если модель вернула один
    assert len(result.set_criteria) == 8

    # итог «yes» при критическом дефекте понижен
    assert result.verdict.ready_for_development == "conditional"

    # нарезка + 2 пакета + набор + склейка дублей + итог
    assert fake_provider.calls == 6


def test_report_has_all_required_sections(sample_path, fake_provider):
    result = Pipeline(fake_provider).run(load_document(sample_path))
    md = render_markdown(result)
    for heading in (
        "## 1. Паспорт прогона",
        "## 2. Как агент нарезал требования",
        "## 3. Проверка каждого требования",
        "## 4. Проверка всего набора требований",
        "## 5. Сводка",
        "## 6. Итог",
    ):
        assert heading in md
    assert "fake-model" in md
    assert "D-001" in md and "Противоречивые" in md
    assert "Цитата не найдена в документе дословно" not in md  # обе цитаты дефектов настоящие


def test_cli_writes_report_and_json(tmp_path, sample_path, fake_provider, monkeypatch):
    monkeypatch.setattr(cli, "create_provider", lambda *a, **k: fake_provider)
    out = tmp_path / "out" / "report.md"
    code = cli.main([str(sample_path), str(out)])
    assert code == 0
    assert out.read_text(encoding="utf-8").startswith("# Отчёт о проверке требований")
    data = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert data["passport"]["document_file"] == "requirements.md"


def test_cli_missing_file_returns_error(tmp_path):
    assert cli.main([str(tmp_path / "nope.pdf"), str(tmp_path / "r.md")]) == 1


def test_parse_json_with_fence():
    assert parse_json('Вот ответ:\n```json\n{"a": 1}\n```') == {"a": 1}


def test_inline_schema_has_no_refs():
    s = json.dumps(inline_schema(RequirementBatchResponse))
    assert "$ref" not in s and "$defs" not in s


def test_score_script(tmp_path, sample_path, fake_provider):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "score", Path(__file__).resolve().parent.parent / "eval" / "score.py"
    )
    score = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(score)

    result = Pipeline(fake_provider).run(load_document(sample_path))
    golden = json.loads((Path(score.__file__).with_name("golden_defects.json")).read_text(encoding="utf-8"))
    res = score.score(json.loads(result.model_dump_json()), golden)
    found = {g["id"] for g in res["found"]}
    assert "G01" in found and "G16" in found


def test_parse_json_with_backticks_inside_strings():
    raw = '{"quote": "```text\nПриложение → AlertsController\n```", "n": 1}'
    assert parse_json(raw)["n"] == 1


def test_merge_duplicates(fake_provider):
    from reqcheck.models import Defect

    def mk(rid, sev, q):
        return Defect(scope="requirement", criterion="CNF", severity=sev, requirement_ids=[rid],
                      quotes=[q], problem=f"GET для сохранения ({rid})", recommendation="POST")

    defects = [mk("R-03", "major", "REST type | GET"), mk("R-01", "critical", "Сохранение порога"),
               mk("R-02", "minor", "REST type | GET")]
    fake_provider._complete_json = lambda *a: '{"groups":[{"defect_ids":["T-001","T-002","T-003"],"reason":"x"}]}'
    merged = Pipeline(fake_provider)._merge_duplicates(defects, {"R-01": 0, "R-02": 1, "R-03": 2})
    assert len(merged) == 1
    d = merged[0]
    assert d.severity == "critical" and d.requirement_ids == ["R-01", "R-02", "R-03"]
    assert d.quotes == ["Сохранение порога", "REST type | GET"]
