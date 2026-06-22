"""Чистые async-функции для долгих операций.

Те же, что в cli.py (_process/_gaps/_draft_related_work/_analyze), но:
- без console.print и typer.Exit
- с callback on_step(str) для прогресса
- возврат dict {"ok": bool, "data": ...} вместо raise

CLI продолжает работать как раньше — он просто вызывает эти функции
и сам печатает результат.
"""
from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable, Optional

# Тип callback-а: получает человекочитаемое описание текущего шага
StepCB = Optional[Callable[[str], Awaitable[None]]]


async def _step(cb: StepCB, msg: str) -> None:
    if cb is not None:
        try:
            await cb(msg)
        except Exception:
            # callback не должен ломать операцию
            pass


# ─────────────────────────────────────────────────────────────────────────────
# process
# ─────────────────────────────────────────────────────────────────────────────
async def process_paper(
    citekey: str, *, only: str = "full", force: bool = False,
    on_step: StepCB = None,
) -> dict:
    """Полный пайплайн для одной статьи.

    Шаги: ocr → figures → index_full → note → index_notes.
    Только-флаги: "markdown" / "full".
    """
    from sciassist.utils.zotero_client import ZoteroClient
    from sciassist.preprocessing.pdf_to_markdown import process_pdf
    from sciassist.vision.figure_extractor import extract_figures
    from sciassist.indexing.rag_indexer import RAGIndexer
    from sciassist.note_generation.obsidian_builder import build_note

    zot = ZoteroClient()
    await _step(on_step, f"ищу {citekey} в Zotero…")
    item = zot.find_by_citekey(citekey)
    if item is None:
        return {"ok": False, "error": f"статья '{citekey}' не найдена в Zotero"}

    pdf = zot.get_pdf_path(item.key)
    if pdf is None:
        return {"ok": False, "error": f"PDF не найден для {citekey}"}

    meta = {
        "title": item.title,
        "year": item.year,
        "doi": item.doi,
        "authors": [{"first": a.first, "last": a.last} for a in item.authors],
    }

    await _step(on_step, "OCR (Olmocr)…")
    md_path = await process_pdf(pdf, citekey, meta)

    if only == "markdown":
        return {"ok": True, "data": {"md": str(md_path)}}

    await _step(on_step, "извлечение фигур (VLM)…")
    figs_path = await extract_figures(pdf, citekey)
    n_figs = len(json.loads(figs_path.read_text()))

    await _step(on_step, "индексация (ChromaDB)…")
    indexer = RAGIndexer()
    n = await indexer.index_paper(citekey, md_path, force=force)

    await _step(on_step, "генерация заметки Obsidian (4 LLM-вызова, ~2 мин)…")
    note_path = await build_note(citekey, item, force=force)

    await _step(on_step, "индексация заметки…")
    n2 = await indexer.index_note(citekey, note_path, force=force)

    return {
        "ok": True,
        "data": {
            "citekey": citekey,
            "md": str(md_path),
            "note": str(note_path),
            "figures": n_figs,
            "chunks_full": n,
            "chunks_notes": n2,
        },
    }


async def process_queue(*, force: bool = False, on_step: StepCB = None) -> dict:
    """Обработать всю Zotero Queue через существующий orchestrator."""
    from sciassist.pipeline.orchestrator import process_queue as _pq

    await _step(on_step, "запускаю process_queue…")
    await _pq(force=force)
    return {"ok": True, "data": {"queue": "done"}}


# ─────────────────────────────────────────────────────────────────────────────
# gaps
# ─────────────────────────────────────────────────────────────────────────────
async def gaps(topic: str, *, papers: int = 10, on_step: StepCB = None) -> dict:
    from sciassist.indexing.rag_indexer import RAGIndexer
    from sciassist.router.model_router import ModelRouter
    from sciassist.utils.lm_studio_client import LMStudioClient
    from sciassist.note_generation.obsidian_builder import (
        _make_messages, _load_prompt, _render_prompt, _parse_json,
    )

    await _step(on_step, f"ищу top-{papers} статей по '{topic}'…")
    indexer = RAGIndexer()
    raw = await indexer.query(topic, top_k=papers * 3, collection="papers_notes")

    metas = raw.get("metadatas", [[]])[0]
    docs  = raw.get("documents",  [[]])[0]

    seen: dict[str, str] = {}
    for meta, doc in zip(metas, docs):
        ck = meta.get("citekey", "")
        if ck and ck not in seen:
            seen[ck] = doc[:800]
        if len(seen) >= papers:
            break

    if not seen:
        return {"ok": False, "error": "нет данных в индексе"}

    context = "\n\n---\n\n".join(f"[{ck}]:\n{text}" for ck, text in seen.items())
    prompt_text = _render_prompt(_load_prompt("gap_analysis"), topic=topic, papers_context=context)

    await _step(on_step, f"gap analysis по {len(seen)} статьям…")
    router = ModelRouter()
    spec = router.select("deep_analysis")
    llm = LMStudioClient()
    raw_resp = await llm.chat(
        messages=_make_messages(prompt_text),
        model=spec.name, temperature=spec.temperature, timeout=spec.timeout,
    )

    parsed = _parse_json(raw_resp)
    if not parsed:
        return {"ok": True, "data": {"raw": raw_resp, "parsed": None}}

    return {"ok": True, "data": {"parsed": parsed, "sources": list(seen.keys())}}


# ─────────────────────────────────────────────────────────────────────────────
# draft related-work
# ─────────────────────────────────────────────────────────────────────────────
async def draft_related_work(
    topic: str, *, papers: int = 10, on_step: StepCB = None,
) -> dict:
    from sciassist.indexing.rag_indexer import RAGIndexer
    from sciassist.router.model_router import ModelRouter
    from sciassist.utils.lm_studio_client import LMStudioClient
    from sciassist.note_generation.obsidian_builder import (
        _make_messages, _load_prompt, _render_prompt,
    )

    await _step(on_step, f"собираю контекст: top-{papers} статей по '{topic}'…")
    indexer = RAGIndexer()
    raw = await indexer.query(topic, top_k=papers * 3, collection="papers_notes")

    metas = raw.get("metadatas", [[]])[0]
    docs  = raw.get("documents",  [[]])[0]

    seen: dict[str, str] = {}
    for meta, doc in zip(metas, docs):
        ck = meta.get("citekey", "")
        if ck and ck not in seen:
            seen[ck] = doc[:600]
        if len(seen) >= papers:
            break

    if not seen:
        return {"ok": False, "error": "нет данных"}

    context = "\n\n".join(f"[{ck}]: {text}" for ck, text in seen.items())
    prompt_text = _render_prompt(_load_prompt("related_work_draft"), topic=topic, papers_context=context)

    await _step(on_step, f"генерирую Related Work ({len(seen)} источников)…")
    router = ModelRouter()
    spec = router.select("deep_analysis")
    llm = LMStudioClient()
    draft = await llm.chat(
        messages=_make_messages(prompt_text),
        model=spec.name, temperature=0.4, timeout=spec.timeout,
    )

    return {"ok": True, "data": {"draft": draft, "sources": list(seen.keys())}}


# ─────────────────────────────────────────────────────────────────────────────
# analyze
# ─────────────────────────────────────────────────────────────────────────────
async def analyze(
    citekey: str, *, mode: str = "critique", on_step: StepCB = None,
) -> dict:
    from sciassist.config import get_settings
    from sciassist.router.model_router import ModelRouter
    from sciassist.utils.lm_studio_client import LMStudioClient
    from sciassist.note_generation.obsidian_builder import (
        _make_messages, _load_prompt, _render_prompt, _parse_json, _truncate,
    )

    cfg = get_settings()
    md = cfg.raw_markdown_path / f"{citekey}.md"
    if not md.exists():
        return {"ok": False, "error": f"markdown не найден. Сначала: sciassist process {citekey}"}

    await _step(on_step, f"анализ '{citekey}' (mode={mode})…")
    full_text = _truncate(md.read_text(encoding="utf-8"), max_words=5000)
    prompt_text = _render_prompt(_load_prompt(mode), paper_text=full_text)

    router = ModelRouter()
    spec = router.select("reasoning")
    llm = LMStudioClient()
    raw = await llm.chat(
        messages=_make_messages(prompt_text),
        model=spec.name, temperature=spec.temperature, timeout=spec.timeout,
    )

    parsed = _parse_json(raw)
    return {"ok": True, "data": {"parsed": parsed, "raw": raw if not parsed else None}}


__all__ = [
    "process_paper", "process_queue",
    "gaps", "draft_related_work", "analyze",
    "StepCB",
]