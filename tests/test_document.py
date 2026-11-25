from pathlib import Path

import pytest

from reqcheck.document import DocumentError, load_document, normalize


def test_load_markdown(sample_path):
    doc = load_document(sample_path)
    assert doc.format == "md"
    assert doc.title == "Уведомление о расходах по карте"
    assert len(doc.sha256) == 64
    assert "   1 | # Уведомление о расходах по карте" in doc.numbered_text()


def test_locate_exact_and_markdown_insensitive(sample_path):
    doc = load_document(sample_path)
    # В исходнике «**Ф-1.** Держатель…» — разметка не должна мешать поиску
    loc = doc.locate("Ф-1. Держатель активной карты KZT задаёт целый порог")
    assert loc is not None and loc.line is not None
    line = doc.text.splitlines()[loc.line - 1]
    assert "Держатель активной карты" in line


def test_locate_table_cell(sample_path):
    doc = load_document(sample_path)
    loc = doc.locate("Расходы 48 500 ₸, порог 50 000 ₸ | Провести покупку на 1 500 ₸")
    assert loc is not None
    assert "48 500" in doc.text.splitlines()[loc.line - 1]


def test_locate_tolerates_small_changes(sample_path):
    doc = load_document(sample_path)
    assert doc.locate("Сервис передает push на отправку в течение минуты после получения события") is not None


def test_locate_rejects_fabricated(sample_path):
    doc = load_document(sample_path)
    assert doc.locate("Система должна поддерживать биометрическую аутентификацию по голосу") is None


def test_normalize():
    assert normalize("**Ф-1.**  «Порог» — ₸") == 'ф-1. "порог" - ₸'


def test_docx(tmp_path: Path):
    import docx

    d = docx.Document()
    d.add_heading("Тестовое ТЗ", level=1)
    d.add_paragraph("Система должна сохранять порог.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Параметр", "Значение"
    t.cell(1, 0).text, t.cell(1, 1).text = "amount", "1 000–1 000 000"
    d.add_paragraph("После таблицы.")
    path = tmp_path / "tz.docx"
    d.save(path)

    doc = load_document(path)
    lines = doc.text.splitlines()
    assert lines[0] == "# Тестовое ТЗ"
    assert "| amount | 1 000–1 000 000 |" in doc.text
    # таблица стоит между абзацами, а не в конце
    assert doc.text.index("сохранять порог") < doc.text.index("amount") < doc.text.index("После таблицы")


def test_unsupported_and_missing(tmp_path: Path):
    with pytest.raises(DocumentError):
        load_document(tmp_path / "nope.md")
    bad = tmp_path / "x.xlsx"
    bad.write_bytes(b"1")
    with pytest.raises(DocumentError):
        load_document(bad)


def test_pdf_pages_and_locate():
    pdf = Path(__file__).parent / "fixtures" / "requirements.pdf"
    doc = load_document(pdf)
    assert doc.format == "pdf" and doc.has_pages
    assert "===== Страница 1 =====" in doc.numbered_text()
    loc = doc.locate("Для чужой карты возвращается 404, как и для отсутствующей карты")
    assert loc is not None and loc.page and loc.page > 1
