"""Split markdown text into overlapping chunks with section tracking."""
import re
from pydantic import BaseModel


class Chunk(BaseModel):
    text: str
    section: str = "body"
    chunk_index: int = 0


# Section name → list of keywords to match
_SECTION_KEYWORDS: list[tuple[str, list[str]]] = [
    ("abstract",     ["abstract"]),
    ("introduction", ["introduction"]),
    ("related_work", ["related work", "background", "prior work"]),
    ("methods",      ["method", "model", "approach", "architecture", "proposed"]),
    ("experiments",  ["experiment", "evaluation", "setup", "implementation"]),
    ("results",      ["result", "performance", "comparison", "analysis"]),
    ("discussion",   ["discussion", "limitation"]),
    ("conclusion",   ["conclusion", "summary", "future work"]),
    ("references",   ["reference", "bibliography"]),
]


def _detect_section(line: str) -> str | None:
    """
    Detect section name from a line.
    Handles: ## Abstract | **Abstract** | 1. Abstract | ABSTRACT
    """
    # Strip markdown: headers (#), bold (**), numbering (1. / 2.1)
    clean = re.sub(r"^#{1,6}\s*", "", line)
    clean = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", clean)
    clean = re.sub(r"^\d+(\.\d+)*\.?\s*", "", clean)
    clean = clean.strip().lower()

    if not clean or len(clean) > 60:   # skip long lines — not a header
        return None

    for section_name, keywords in _SECTION_KEYWORDS:
        for kw in keywords:
            if clean.startswith(kw) or clean == kw:
                return section_name
    return None


def chunk_markdown(
    text: str,
    chunk_size: int = 1200,   # target words per chunk
    overlap: int = 200,        # overlap words between chunks
) -> list[Chunk]:
    """
    Split markdown into overlapping word-count chunks, tracking section.
    """
    lines = text.split("\n")
    chunks: list[Chunk] = []

    current_section = "preamble"
    passed_frontmatter = False
    word_buffer: list[str] = []
    overlap_tail: list[str] = []

    for line in lines:
        # Skip YAML frontmatter block (--- ... ---)
        if line.strip() == "---":
            passed_frontmatter = not passed_frontmatter
            continue
        if not passed_frontmatter and not chunks and not word_buffer:
            continue   # still inside frontmatter

        # Section detection
        detected = _detect_section(line)
        if detected:
            # Flush current buffer before switching section
            if word_buffer:
                chunk_text = " ".join(overlap_tail + word_buffer).strip()
                if chunk_text:
                    chunks.append(Chunk(
                        text=chunk_text,
                        section=current_section,
                        chunk_index=len(chunks),
                    ))
                overlap_tail = word_buffer[-overlap:]
                word_buffer = []
            current_section = detected

        word_buffer.extend(line.split())

        if len(word_buffer) >= chunk_size:
            chunk_text = " ".join(overlap_tail + word_buffer).strip()
            chunks.append(Chunk(
                text=chunk_text,
                section=current_section,
                chunk_index=len(chunks),
            ))
            overlap_tail = word_buffer[-overlap:]
            word_buffer = []

    # Flush remainder
    if word_buffer or overlap_tail:
        chunk_text = " ".join(overlap_tail + word_buffer).strip()
        if chunk_text:
            chunks.append(Chunk(
                text=chunk_text,
                section=current_section,
                chunk_index=len(chunks),
            ))

    return chunks
