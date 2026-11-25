"""Чтение входного документа и поиск цитат в нём.

Поддерживаемые форматы: PDF, DOCX, Markdown, TXT.
Страницы PDF разделяются символом ``\\f`` — по нему считаются номера страниц.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from .models import Location

PAGE_BREAK = "\f"
SUPPORTED = {".pdf", ".docx", ".md", ".markdown", ".txt"}


class DocumentError(Exception):
    pass


@dataclass
class Document:
    path: Path
    text: str
    sha256: str
    format: str
    title: str
    has_pages: bool = False
    _index: "_QuoteIndex | None" = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return self.path.name

    def numbered_text(self) -> str:
        """Текст с номерами строк (и страниц для PDF) — так модель может ссылаться на место."""
        out = []
        for page_no, page in enumerate(self.text.split(PAGE_BREAK), start=1):
            if self.has_pages:
                out.append(f"===== Страница {page_no} =====")
            for line_no, line in enumerate(page.splitlines(), start=1):
                out.append(f"{line_no:>4} | {line}")
        return "\n".join(out)

    def locate(self, quote: str) -> Location | None:
        """Находит цитату в тексте. Возвращает место или None, если цитаты в документе нет."""
        if self._index is None:
            self._index = _QuoteIndex(self.text, self.has_pages)
        return self._index.locate(quote)


# ---------------------------------------------------------------------------
# Загрузка
# ---------------------------------------------------------------------------

def load_document(path: str | Path) -> Document:
    p = Path(path)
    if not p.exists():
        raise DocumentError(f"Файл не найден: {p}")
    ext = p.suffix.lower()
    if ext not in SUPPORTED:
        raise DocumentError(
            f"Формат {ext or '(без расширения)'} не поддерживается. Поддерживаются: "
            + ", ".join(sorted(SUPPORTED))
        )
    raw = p.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()

    has_pages = False
    if ext == ".pdf":
        text = _read_pdf(p)
        has_pages = True
    elif ext == ".docx":
        text = _read_docx(p)
    else:
        text = _decode(raw)

    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    if not text.replace(PAGE_BREAK, "").strip():
        raise DocumentError(
            f"Из файла {p.name} не удалось извлечь текст. Если это скан, сначала распознайте его (OCR)."
        )
    return Document(
        path=p, text=text, sha256=sha, format=ext.lstrip("."),
        title=_guess_title(text, p), has_pages=has_pages,
    )


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_pdf(p: Path) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(p))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:  # pypdf бросает много разных исключений
        raise DocumentError(f"Не удалось прочитать PDF {p.name}: {exc}") from exc
    return PAGE_BREAK.join(pages)


def _read_docx(p: Path) -> str:
    import docx
    from docx.oxml.ns import qn

    try:
        d = docx.Document(str(p))
    except Exception as exc:
        raise DocumentError(f"Не удалось прочитать DOCX {p.name}: {exc}") from exc

    # Идём по телу документа по порядку, чтобы таблицы оставались на своих местах.
    lines: list[str] = []
    paragraphs = {id(par._element): par for par in d.paragraphs}
    tables = {id(t._element): t for t in d.tables}
    for child in d.element.body.iterchildren():
        if child.tag == qn("w:p"):
            par = paragraphs.get(id(child))
            if par is None:
                continue
            style = (par.style.name or "").lower() if par.style is not None else ""
            text = par.text
            m = re.search(r"heading (\d)", style)
            if m and text.strip():
                text = "#" * int(m.group(1)) + " " + text
            elif "list" in style and text.strip():
                text = "* " + text
            lines.append(text)
        elif child.tag == qn("w:tbl"):
            table = tables.get(id(child))
            if table is None:
                continue
            for row in table.rows:
                cells = [c.text.replace("\n", " ").strip() for c in row.cells]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")
    return "\n".join(lines)


def _guess_title(text: str, p: Path) -> str:
    for line in text.replace(PAGE_BREAK, "\n").splitlines():
        s = line.strip()
        if not s:
            continue
        return s.lstrip("#").strip() or p.stem
    return p.stem


# ---------------------------------------------------------------------------
# Поиск цитат
# ---------------------------------------------------------------------------

_DROP = set("*`\\_|#>")
_MAP = {
    "ё": "е", "«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "'": '"', "’": '"',
    "–": "-", "—": "-", "−": "-", "‑": "-", " ": " ", "\t": " ",
}


def normalize(s: str) -> str:
    """Нормализация для сравнения: регистр, кавычки, тире, разметка Markdown, пробелы."""
    out = []
    prev_space = True
    for ch in s.lower():
        ch = _MAP.get(ch, ch)
        if ch in _DROP or ch.isspace():
            if not prev_space:
                out.append(" ")
                prev_space = True
            continue
        out.append(ch)
        prev_space = False
    return "".join(out).strip()


class _QuoteIndex:
    def __init__(self, text: str, has_pages: bool):
        self.has_pages = has_pages
        chars: list[str] = []
        pos: list[tuple[int, int]] = []  # (страница, строка) для каждого символа нормализованного текста
        page, line = 1, 1
        prev_space = True
        for ch in text.lower():
            if ch == PAGE_BREAK:
                page, line = page + 1, 1
                ch = " "
            elif ch == "\n":
                line += 1
                ch = " "
            ch = _MAP.get(ch, ch)
            if ch in _DROP or ch.isspace():
                if not prev_space:
                    chars.append(" ")
                    pos.append((page, line))
                    prev_space = True
                continue
            chars.append(ch)
            pos.append((page, line))
            prev_space = False
        self.norm = "".join(chars)
        self.pos = pos

    def _loc(self, idx: int) -> Location:
        page, line = self.pos[min(idx, len(self.pos) - 1)]
        return Location(page=page if self.has_pages else None, line=line)

    def locate(self, quote: str) -> Location | None:
        q = normalize(quote).strip(' ."')
        if len(q) < 3 or not self.norm:
            return None
        idx = self.norm.find(q)
        if idx >= 0:
            return self._loc(idx)
        # Модель могла слегка изменить цитату (пропустить слово, поменять падеж).
        # Ищем самый длинный общий фрагмент и принимаем, если он покрывает большую часть цитаты.
        if len(q) > 2000:
            return None
        sm = SequenceMatcher(None, self.norm, q, autojunk=False)
        m = sm.find_longest_match(0, len(self.norm), 0, len(q))
        if m.size >= max(12, int(len(q) * 0.6)):
            return self._loc(m.a)
        return None
