"""Split markdown text into overlapping chunks with section tracking."""
import re
from pydantic import BaseModel


class Chunk(BaseModel):
    text: str
    section: str = "body"
    chunk_index: int = 0


_SECTION_KEYWORDS: list[tuple[str, list[str]]] = [
    ("abstract",     ["abstract"]),
    ("introduction", ["introduction"]),
    ("related_work", ["related work", "background", "prior work"]),
    ("methods",      ["method", "methodology", "approach", "architecture"]),
    ("experiments",  ["experiment", "experimental", "evaluation"]),
    ("results",      ["result", "findings"]),
    ("discussion",   ["discussion", "limitation"]),
    ("conclusion",   ["conclusion", "concluding", "future work"]),
    ("references",   ["reference", "bibliography"]),
]

_MAX_HEADER_WORDS = 6


def _strip_frontmatter(text: str) -> str:
    """Удалить YAML-frontmatter в начале документа (---...---)."""
    m = re.match(r"^\s*---\s*\n.*?\n---\s*\n", text, re.DOTALL)
    return text[m.end():] if m else text


def _detect_section(line: str) -> str | None:
    """Определить секцию ТОЛЬКО для строк, похожих на заголовок."""
    raw = line.strip()
    if not raw:
        return None

    is_md_header = bool(re.match(r"^#{1,6}\s+\S", raw))
    is_bold_only = bool(re.match(r"^\*{1,2}[^*]+\*{1,2}[:.]?\s*$", raw))
    is_numbered  = bool(re.match(r"^\d+(\.\d+)*\.?\s+[A-Za-z]", raw))

    clean = re.sub(r"^#{1,6}\s*", "", raw)
    clean = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", clean)
    clean = re.sub(r"^\d+(\.\d+)*\.?\s*", "", clean)
    clean = clean.strip().rstrip(":.").lower()
    if not clean:
        return None

    words = clean.split()
    if len(words) > _MAX_HEADER_WORDS:        # длинная строка — не заголовок
        return None

    # Заголовок: явный маркер (#, **, нумерация) ИЛИ очень короткая строка
    heading_like = is_md_header or is_bold_only or is_numbered or len(words) <= 3
    if not heading_like:
        return None

    for section_name, keywords in _SECTION_KEYWORDS:
        for kw in keywords:
            kw_words = kw.split()
            if words[: len(kw_words)] == kw_words and len(words) - len(kw_words) <= 3:
                return section_name
    return None


def chunk_markdown(
    text: str,
    chunk_size: int = 1200,
    overlap: int = 200,
) -> list[Chunk]:
    """Split markdown into overlapping word-count chunks, tracking section."""
    text = _strip_frontmatter(text)
    lines = text.split("\n")
    chunks: list[Chunk] = []

    current_section = "preamble"
    word_buffer: list[str] = []
    overlap_tail: list[str] = []

    def _flush() -> None:
        nonlocal word_buffer, overlap_tail
        if not word_buffer:
            return
        chunk_text = " ".join(overlap_tail + word_buffer).strip()
        if chunk_text:
            chunks.append(Chunk(
                text=chunk_text,
                section=current_section,
                chunk_index=len(chunks),
            ))
        overlap_tail = word_buffer[-overlap:]
        word_buffer = []

    for line in lines:
        if line.strip() == "---":      # разделители страниц / hr — пропустить
            continue

        detected = _detect_section(line)
        if detected:
            _flush()
            current_section = detected
            continue

        word_buffer.extend(line.split())
        if len(word_buffer) >= chunk_size:
            _flush()

    # Хвост
    if word_buffer or overlap_tail:
        chunk_text = " ".join(overlap_tail + word_buffer).strip()
        if chunk_text:
            chunks.append(Chunk(
                text=chunk_text,
                section=current_section,
                chunk_index=len(chunks),
            ))

    return chunks
