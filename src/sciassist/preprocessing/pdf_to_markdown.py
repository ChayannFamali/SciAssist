"""
PDF → Markdown.

Стратегия (ocr_mode в settings.yaml):
  auto    — PyMuPDF если есть встроенный текст, Olmocr если скан
  pymupdf — всегда прямое извлечение (цифровые PDF, быстро)
  olmocr  — всегда через LLM (сканы, медленно, последовательно)

Olmocr требует anchor text (текст из PDF слоя) — передаём его в промпт.
"""
import asyncio
import tempfile
from collections import Counter                          # ← добавлено
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF
from loguru import logger

from sciassist.config import get_settings, get_yaml_config
from sciassist.exceptions import PDFProcessingError
from sciassist.router.model_router import ModelRouter
from sciassist.utils.lm_studio_client import LMStudioClient


# ---------------------------------------------------------------------------
# PyMuPDF extraction (primary for digital PDFs)
# ---------------------------------------------------------------------------

def _body_font_size(doc) -> float:
    """Самый частый размер шрифта (по объёму текста) = тело документа."""
    sizes: Counter = Counter()
    for page in doc:
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    txt = span.get("text", "").strip()
                    if txt:
                        sizes[round(span["size"])] += len(txt)
    return float(sizes.most_common(1)[0][0]) if sizes else 10.0


def _line_to_md(text: str, max_size: float, is_bold: bool, body: float) -> str:
    """Решить, заголовок строка или абзац, и вернуть markdown."""
    words = text.split()
    if len(words) <= 12 and max_size >= body + 1:
        if max_size >= body + 4:
            return f"\n# {text}\n"
        if max_size >= body + 2:
            return f"\n## {text}\n"
        return f"\n### {text}\n"
    if len(words) <= 8 and is_bold and text[:1].isupper():
        return f"\n### {text}\n"
    return text


def _pymupdf_pages(pdf_path: Path) -> list[str]:
    """Извлечь текст с разметкой заголовков по размеру шрифта."""
    cfg = get_yaml_config().get("preprocessing", {})
    detect = cfg.get("detect_headings", True)

    doc = fitz.open(str(pdf_path))
    try:
        if not detect:                                  # старое поведение
            return [p.get_text("text") for p in doc]

        body = _body_font_size(doc)
        pages: list[str] = []
        for page in doc:
            out: list[str] = []
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") != 0:              # пропустить картинки
                    continue
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    text = "".join(s.get("text", "") for s in spans).strip()
                    if not text:
                        continue
                    max_size = max((round(s["size"]) for s in spans), default=body)
                    is_bold = any(s.get("flags", 0) & 16 for s in spans)  # bit4 = bold
                    out.append(_line_to_md(text, max_size, is_bold, body))
            pages.append("\n".join(out))
        return pages
    finally:
        doc.close()


def _is_scanned(pages: list[str], threshold: int) -> bool:
    """True if average chars/page is below threshold → likely a scan."""
    if not pages:
        return True
    avg = sum(len(p.strip()) for p in pages) / len(pages)
    return avg < threshold


# ---------------------------------------------------------------------------
# Olmocr (fallback for scanned PDFs)
# ---------------------------------------------------------------------------

def _render_page(pdf_path: Path, page_num: int, dpi: int, out_dir: Path) -> Path:
    """Render a single PDF page to PNG."""
    doc = fitz.open(str(pdf_path))
    page = doc[page_num]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    out = out_dir / f"page_{page_num:04d}.png"
    pix.save(str(out))
    doc.close()
    return out


async def _olmocr_page(
    pdf_path: Path,
    page_num: int,
    anchor_text: str,
    client: LMStudioClient,
    model: str,
    dpi: int,
    tmp_dir: Path,
) -> str:
    """
    OCR one page via Olmocr.
    Anchor text = existing text from PDF layer (may be empty for scans).
    """
    image = await asyncio.to_thread(_render_page, pdf_path, page_num, dpi, tmp_dir)

    anchor_block = f"<page_content>\n{anchor_text}\n</page_content>\n\n" if anchor_text.strip() else ""
    prompt = (
        f"{anchor_block}"
        "Convert this document page image to clean markdown. "
        "Preserve all text, tables (markdown format), and equations ($LaTeX$). "
        "Output only markdown, no commentary."
    )

    try:
        result = await client.vision_chat([image], prompt, model)
        logger.debug(f"  Olmocr стр.{page_num + 1}: {len(result)} симв.")
        return result
    except Exception as e:
        logger.warning(f"  Olmocr failed стр.{page_num + 1}: {e}")
        return f"\n<!-- OCR_FAILED_PAGE_{page_num + 1} -->\n"


async def _olmocr_all(
    pdf_path: Path,
    anchor_pages: list[str],
    dpi: int,
    model: str,
) -> list[str]:
    """Sequential Olmocr for all pages (concurrency=1 to avoid crashes)."""
    client = LMStudioClient()
    results: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for i, anchor in enumerate(anchor_pages):
            logger.info(f"  Olmocr страница {i + 1}/{len(anchor_pages)}…")
            text = await _olmocr_page(pdf_path, i, anchor, client, model, dpi, tmp_dir)
            results.append(text)

    return results


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------

def _frontmatter(citekey: str, meta: dict, method: str) -> str:
    authors = ", ".join(
        f"{a.get('first', '')} {a.get('last', '')}".strip()
        for a in meta.get("authors", [])
    )
    title = str(meta.get("title", "")).replace('"', "'")
    return (
        f"---\n"
        f"citekey: {citekey}\n"
        f'title: "{title}"\n'
        f"authors: [{authors}]\n"
        f"year: {meta.get('year', '')}\n"
        f"doi: {meta.get('doi', '') or ''}\n"
        f"processed_at: {datetime.now(timezone.utc).isoformat()}\n"
        f"extraction_method: {method}\n"
        f"---\n\n"
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def process_pdf(
    pdf_path: Path,
    citekey: str,
    zotero_meta: dict | None = None,
) -> Path:
    """
    PDF → Markdown. Saves to data/raw_markdown/{citekey}.md.
    Returns output path.
    """
    cfg = get_settings()
    prep = get_yaml_config().get("preprocessing", {})
    out = cfg.raw_markdown_path / f"{citekey}.md"
    cfg.raw_markdown_path.mkdir(parents=True, exist_ok=True)

    try:
        doc = fitz.open(str(pdf_path))
        if doc.is_encrypted:
            raise PDFProcessingError(f"PDF зашифрован: {pdf_path}")
        n_pages = len(doc)
        doc.close()
    except PDFProcessingError:
        raise
    except Exception as e:
        raise PDFProcessingError(f"Не могу открыть PDF: {e}") from e

    logger.info(f"{citekey}: обработка PDF ({n_pages} стр.) — {pdf_path.name}")

    anchor_pages = await asyncio.to_thread(_pymupdf_pages, pdf_path)

    mode = prep.get("ocr_mode", "auto")
    threshold = prep.get("scanned_threshold_chars_per_page", 100)

    use_olmocr = (
        mode == "olmocr"
        or (mode == "auto" and _is_scanned(anchor_pages, threshold))
    )

    if use_olmocr:
        logger.info(f"{citekey}: режим Olmocr (скан или принудительно)")
        router = ModelRouter()
        spec = router.select("ocr")
        dpi = prep.get("page_dpi", 150)
        page_texts = await _olmocr_all(pdf_path, anchor_pages, dpi, spec.name)
        method = f"olmocr:{spec.name}"
    else:
        avg_chars = sum(len(p.strip()) for p in anchor_pages) / max(len(anchor_pages), 1)
        logger.info(f"{citekey}: режим PyMuPDF (≈{avg_chars:.0f} симв./стр.)")
        page_texts = anchor_pages
        method = "pymupdf"

    full_text = "\n\n---\n\n".join(page_texts)

    min_len = prep.get("min_text_length_warn", 500)
    if len(full_text) < min_len:
        logger.warning(f"{citekey}: короткий результат ({len(full_text)} симв.) — проверь PDF")

    meta = zotero_meta or {}
    out.write_text(_frontmatter(citekey, meta, method) + full_text, encoding="utf-8")
    logger.info(f"{citekey}: готово → {out} ({len(full_text)} симв., метод={method})")
    return out
