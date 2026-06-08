"""SciAssist CLI."""
import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

app = typer.Typer(name="sciassist", help="Local AI assistant for scientific research.", add_completion=False)
console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# health
# ─────────────────────────────────────────────────────────────────────────────
@app.command()
def health() -> None:
    """Check Zotero, LM Studio, ChromaDB, paths."""
    import subprocess, sys
    script = Path(__file__).parent.parent.parent / "scripts" / "healthcheck.py"
    subprocess.run([sys.executable, str(script)], check=False)


# ─────────────────────────────────────────────────────────────────────────────
# process
# ─────────────────────────────────────────────────────────────────────────────
@app.command()
def process(
    citekey: Annotated[str | None, typer.Argument(help="Citekey для одной статьи")] = None,
    queue: Annotated[bool, typer.Option("--queue", help="Обработать всю SciAssist Queue")] = False,
    only: Annotated[str, typer.Option(help="markdown | full")] = "full",
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Process paper(s): one by citekey or entire Zotero Queue."""
    if queue:
        asyncio.run(_process_queue(force=force))
    elif citekey:
        asyncio.run(_process(citekey.lstrip("@"), only=only, force=force))
    else:
        console.print("[red]Укажи citekey или --queue[/red]")
        raise typer.Exit(1)


async def _process(citekey: str, only: str, force: bool) -> None:
    from sciassist.utils.logging import setup_logging
    from sciassist.utils.zotero_client import ZoteroClient
    from sciassist.preprocessing.pdf_to_markdown import process_pdf
    setup_logging()

    zot = ZoteroClient()
    console.print(f"[cyan]Ищу '{citekey}' в Zotero…[/cyan]")

    item = zot.find_by_citekey(citekey)
    if item is None:
        console.print(f"[red]❌ Статья '{citekey}' не найдена в Zotero[/red]")
        raise typer.Exit(1)

    pdf = zot.get_pdf_path(item.key)
    if pdf is None:
        console.print(f"[red]❌ PDF не найден для {citekey}[/red]")
        raise typer.Exit(1)

    console.print(Panel(
        f"[bold]{item.title}[/bold]\n"
        f"[dim]{', '.join(str(a) for a in item.authors[:3])} ({item.year})[/dim]\n"
        f"PDF: {pdf}",
        title=citekey,
    ))

    meta = {
        "title": item.title,
        "year": item.year,
        "doi": item.doi,
        "authors": [{"first": a.first, "last": a.last} for a in item.authors],
    }

    # Step 1: OCR
    console.print("[cyan]→ OCR (Olmocr)…[/cyan]")
    md_path = await process_pdf(pdf, citekey, meta)
    console.print(f"[green]✓ Markdown:[/green] {md_path}")

    if only == "markdown":
        return

    # Step 2: Figures
    console.print("[cyan]→ Извлечение фигур (VLM)…[/cyan]")
    from sciassist.vision.figure_extractor import extract_figures
    figs_path = await extract_figures(pdf, citekey)
    n_figs = len(json.loads(figs_path.read_text()))
    console.print(f"[green]✓ {n_figs} фигур[/green]")

    # Step 3: Index paper
    console.print("[cyan]→ Индексация (ChromaDB)…[/cyan]")
    from sciassist.indexing.rag_indexer import RAGIndexer
    indexer = RAGIndexer()
    n = await indexer.index_paper(citekey, md_path, force=force)
    console.print(f"[green]✓ {n} чанков → papers_full[/green]")

    # Step 4: Obsidian note
    console.print("[cyan]→ Генерация заметки Obsidian… (4 LLM-вызова ~2 мин)[/cyan]")
    from sciassist.note_generation.obsidian_builder import build_note
    note_path = await build_note(citekey, item, force=force)
    console.print(f"[green]✓ Заметка:[/green] {note_path}")

    # Step 5: Index note
    n = await indexer.index_note(citekey, note_path, force=force)
    console.print(f"[green]✓ {n} чанков → papers_notes[/green]")


async def _process_queue(force: bool) -> None:
    from sciassist.utils.logging import setup_logging
    from sciassist.pipeline.orchestrator import process_queue
    setup_logging()
    await process_queue(force=force)


# ─────────────────────────────────────────────────────────────────────────────
# note
# ─────────────────────────────────────────────────────────────────────────────
@app.command()
def note(
    citekey: Annotated[str, typer.Argument(help="Citekey")],
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Generate (or regenerate) Obsidian note for a paper."""
    asyncio.run(_note(citekey.lstrip("@"), force=force))


async def _note(citekey: str, force: bool) -> None:
    from sciassist.utils.logging import setup_logging
    from sciassist.utils.zotero_client import ZoteroClient
    from sciassist.note_generation.obsidian_builder import build_note
    setup_logging()

    zot = ZoteroClient()
    item = zot.find_by_citekey(citekey)
    if item is None:
        console.print(f"[red]❌ '{citekey}' не найден в Zotero[/red]")
        raise typer.Exit(1)

    console.print(f"[cyan]Генерирую заметку для '{citekey}'… (4 LLM-вызова)[/cyan]")
    path = await build_note(citekey, item, force=force)
    console.print(f"[green]✓ Заметка:[/green] {path}")


# ─────────────────────────────────────────────────────────────────────────────
# search
# ─────────────────────────────────────────────────────────────────────────────
@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Search query")],
    top: Annotated[int, typer.Option("--top", "-k")] = 5,
    col: Annotated[str, typer.Option("--col")] = "papers_full",
) -> None:
    """Semantic search — raw retrieval, no LLM generation."""
    asyncio.run(_search(query, top, col))


async def _search(query: str, top: int, col: str) -> None:
    from sciassist.utils.logging import setup_logging
    from sciassist.indexing.rag_indexer import RAGIndexer
    setup_logging()

    indexer = RAGIndexer()
    raw = await indexer.query(query, top_k=top, collection=col)

    docs = raw.get("documents", [[]])[0]
    metas = raw.get("metadatas", [[]])[0]
    dists = raw.get("distances", [[]])[0]

    if not docs:
        console.print("[yellow]Нет результатов — индекс пуст?[/yellow]")
        return

    t = Table("#", "Citekey", "Section", "Score", "Preview", show_lines=True, expand=True)
    for i, (doc, meta, dist) in enumerate(zip(docs, metas, dists)):
        score = 1 - float(dist)
        preview = doc[:100].replace("\n", " ") + "…"
        # подсветка: что прошло бы порог 0.4
        score_str = f"[green]{score:.3f}[/green]" if score >= 0.4 else f"[dim]{score:.3f}[/dim]"
        t.add_row(str(i + 1), meta.get("citekey", "?"), meta.get("section", "?"), score_str, preview)
    console.print(t)


# ─────────────────────────────────────────────────────────────────────────────
# ask
# ─────────────────────────────────────────────────────────────────────────────
@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="Question for RAG")],
    top: Annotated[int, typer.Option("--top", "-k")] = 5,
    min_score: Annotated[float, typer.Option("--min-score", "-s")] = 0.4,
    max_per_paper: Annotated[int, typer.Option("--max-per-paper", "-m")] = 3,
    rerank: Annotated[bool, typer.Option("--rerank/--no-rerank")] = True,
    hybrid: Annotated[bool, typer.Option("--hybrid/--no-hybrid")] = True,
    col: Annotated[str, typer.Option("--col", "-c",
        help="papers_full | papers_notes | both")] = "papers_full",
    hyde: Annotated[bool, typer.Option("--hyde/--no-hyde",   # ← добавлено
        help="HyDE: генерировать гипотетический ответ для эмбеддинга")] = False,
) -> None:
    """Ask a question — RAG answer with citations."""
    asyncio.run(_ask(question, top, min_score, max_per_paper, rerank, hybrid, col, hyde))  # ← добавлен hyde


async def _ask(question, top, min_score, max_per_paper, rerank, hybrid, col, hyde) -> None:  # ← добавлен hyde
    from sciassist.utils.logging import setup_logging
    from sciassist.rag.query_engine import QueryEngine
    setup_logging()

    console.print(
        f"[dim]retrieve (top={top}, min={min_score}, max/paper={max_per_paper}, "
        f"rerank={'on' if rerank else 'off'}, hybrid={'on' if hybrid else 'off'}, "
        f"col={col}, hyde={'on' if hyde else 'off'})…[/dim]"  # ← добавлен hyde
    )
    engine = QueryEngine()
    result = await engine.ask(
        question, top_k=top, min_score=min_score,
        max_per_paper=max_per_paper, rerank=rerank, hybrid=hybrid,
        collection=col, hyde=hyde,                             # ← добавлен hyde
    )
    console.print()
    console.print(Panel(result.answer, title="[bold cyan]Ответ[/bold cyan]"))

    t = Table("Citekey", "Section", "Score", title="Источники")
    for s in result.sources:
        section_label = s.section
        if col == "both":
            tag = "[note]" if "note" in s.section.lower() else "[full]"
            section_label = f"{tag} {s.section}"
        t.add_row(s.citekey, section_label, str(s.score))
    console.print(t)
    console.print(f"[dim]Модель: {result.model}[/dim]")

# ─────────────────────────────────────────────────────────────────────────────
# stats
# ─────────────────────────────────────────────────────────────────────────────
@app.command()
def stats() -> None:
    """Library statistics."""
    from sciassist.indexing.rag_indexer import RAGIndexer
    s = RAGIndexer().stats()
    t = Table("Параметр", "Значение")
    for k, v in s.items():
        t.add_row(k, str(v))
    console.print(t)


# ─────────────────────────────────────────────────────────────────────────────
# logs
# ─────────────────────────────────────────────────────────────────────────────
@app.command()
def logs(tail: Annotated[int, typer.Option("--tail", "-n")] = 20) -> None:
    """Show last N LLM calls from llm_calls.jsonl."""
    from sciassist.config import get_settings
    log_file = get_settings().logs_path / "llm_calls.jsonl"
    if not log_file.exists():
        console.print("[yellow]llm_calls.jsonl не найден[/yellow]")
        return
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()[-tail:]
    t = Table("Time", "Model", "Task", "Tokens↑", "Tokens↓", "ms", show_lines=False)
    for line in lines:
        try:
            r = json.loads(line)
            t.add_row(
                r.get("ts", "")[-8:-5],  # just HH:MM
                r.get("model", "?")[:30],
                r.get("task", "?"),
                str(r.get("prompt_tokens", "")),
                str(r.get("completion_tokens", "")),
                str(r.get("duration_ms", "")),
            )
        except Exception:
            pass
    console.print(t)


# ─────────────────────────────────────────────────────────────────────────────
# similar
# ─────────────────────────────────────────────────────────────────────────────
@app.command()
def similar(
    citekey: Annotated[str, typer.Argument()],
    top: Annotated[int, typer.Option("--top", "-k")] = 10,
) -> None:
    """Find papers similar to @citekey by embedding proximity."""
    asyncio.run(_similar(citekey.lstrip("@"), top))


async def _similar(citekey: str, top: int) -> None:
    from sciassist.utils.logging import setup_logging
    from sciassist.indexing.rag_indexer import RAGIndexer
    setup_logging()

    cfg = __import__("sciassist.config", fromlist=["get_settings"]).get_settings()
    md = cfg.raw_markdown_path / f"{citekey}.md"
    if not md.exists():
        console.print(f"[red]Сначала обработай статью: sciassist process {citekey}[/red]")
        raise typer.Exit(1)

    # First 600 words of paper as similarity query
    query = " ".join(md.read_text(encoding="utf-8").split()[:600])

    indexer = RAGIndexer()
    raw = await indexer.query(query, top_k=top * 4)

    docs = raw.get("documents", [[]])[0]
    metas = raw.get("metadatas", [[]])[0]
    dists = raw.get("distances", [[]])[0]

    seen: set[str] = {citekey}
    rows = []
    for meta, dist in zip(metas, dists):
        ck = meta.get("citekey", "")
        if ck in seen:
            continue
        seen.add(ck)
        rows.append((ck, meta.get("section", ""), round(1 - float(dist), 3)))
        if len(rows) >= top:
            break

    if not rows:
        console.print("[yellow]Нет похожих статей в индексе[/yellow]")
        return

    t = Table("Citekey", "Section", "Score", title=f"Похожие на {citekey}")
    for ck, sec, score in rows:
        t.add_row(ck, sec, str(score))
    console.print(t)


# ─────────────────────────────────────────────────────────────────────────────
# gaps
# ─────────────────────────────────────────────────────────────────────────────
@app.command()
def gaps(
    topic: Annotated[str, typer.Argument(help="Тема для gap analysis")],
    papers: Annotated[int, typer.Option("--papers", "-n")] = 10,
) -> None:
    """Find research gaps in the library on a given topic."""
    asyncio.run(_gaps(topic, papers))


async def _gaps(topic: str, n: int) -> None:
    from sciassist.utils.logging import setup_logging
    from sciassist.indexing.rag_indexer import RAGIndexer
    from sciassist.router.model_router import ModelRouter
    from sciassist.utils.lm_studio_client import LMStudioClient
    from sciassist.note_generation.obsidian_builder import _make_messages, _load_prompt, _render_prompt
    setup_logging()

    console.print(f"[cyan]Ищу top-{n} статей по теме '{topic}'…[/cyan]")
    indexer = RAGIndexer()
    raw = await indexer.query(topic, top_k=n * 3, collection="papers_notes")

    # Collect unique citekeys + note previews
    metas = raw.get("metadatas", [[]])[0]
    docs  = raw.get("documents",  [[]])[0]

    seen: dict[str, str] = {}
    for meta, doc in zip(metas, docs):
        ck = meta.get("citekey", "")
        if ck and ck not in seen:
            seen[ck] = doc[:800]
        if len(seen) >= n:
            break

    if not seen:
        console.print("[yellow]Нет данных в индексе[/yellow]")
        return

    context = "\n\n---\n\n".join(f"[{ck}]:\n{text}" for ck, text in seen.items())
    prompt_text = _render_prompt(_load_prompt("gap_analysis"), topic=topic, papers_context=context)

    console.print(f"[cyan]Gap analysis по {len(seen)} статьям…[/cyan]")
    router = ModelRouter()
    spec = router.select("deep_analysis")
    llm = LMStudioClient()
    raw_resp = await llm.chat(
        messages=_make_messages(prompt_text),
        model=spec.name, temperature=spec.temperature, timeout=spec.timeout,
    )

    from sciassist.note_generation.obsidian_builder import _parse_json
    result = _parse_json(raw_resp)

    if not result:
        console.print(raw_resp)
        return

    for key, label in [
        ("open_problems",        "🔴 Нерешённые проблемы"),
        ("promising_directions", "🟢 Перспективные направления"),
        ("contradictions",       "⚠️  Противоречия"),
        ("missing_experiments",  "🔬 Недостающие эксперименты"),
    ]:
        items = result.get(key, [])
        if items:
            console.print(f"\n[bold]{label}[/bold]")
            for item in items:
                console.print(f"  • {item}")


# ─────────────────────────────────────────────────────────────────────────────
# draft subcommands
# ─────────────────────────────────────────────────────────────────────────────
draft_app = typer.Typer(name="draft", help="Draft generation commands.")
app.add_typer(draft_app, name="draft")


@draft_app.command("related-work")
def draft_related_work(
    topic: Annotated[str, typer.Argument(help="Тема раздела")],
    papers: Annotated[int, typer.Option("--papers", "-n")] = 10,
) -> None:
    """Generate a Related Work section draft with \\cite{citekey} references."""
    asyncio.run(_draft_related_work(topic, papers))


async def _draft_related_work(topic: str, n: int) -> None:
    from sciassist.utils.logging import setup_logging
    from sciassist.indexing.rag_indexer import RAGIndexer
    from sciassist.router.model_router import ModelRouter
    from sciassist.utils.lm_studio_client import LMStudioClient
    from sciassist.note_generation.obsidian_builder import _make_messages, _load_prompt, _render_prompt, _parse_json
    setup_logging()

    console.print(f"[cyan]Собираю контекст: top-{n} статей по '{topic}'…[/cyan]")
    indexer = RAGIndexer()
    raw = await indexer.query(topic, top_k=n * 3, collection="papers_notes")

    metas = raw.get("metadatas", [[]])[0]
    docs  = raw.get("documents",  [[]])[0]

    seen: dict[str, str] = {}
    for meta, doc in zip(metas, docs):
        ck = meta.get("citekey", "")
        if ck and ck not in seen:
            seen[ck] = doc[:600]
        if len(seen) >= n:
            break

    if not seen:
        console.print("[yellow]Нет данных[/yellow]")
        return

    context = "\n\n".join(f"[{ck}]: {text}" for ck, text in seen.items())
    prompt_text = _render_prompt(_load_prompt("related_work_draft"), topic=topic, papers_context=context)

    console.print(f"[cyan]Генерирую Related Work ({len(seen)} источников)…[/cyan]")
    router = ModelRouter()
    spec = router.select("deep_analysis")
    llm = LMStudioClient()
    draft = await llm.chat(
        messages=_make_messages(prompt_text),
        model=spec.name, temperature=0.4, timeout=spec.timeout,
    )

    console.print(Panel(draft, title=f"[bold]Related Work: {topic}[/bold]"))
    console.print(f"\n[dim]Источники: {', '.join(seen.keys())}[/dim]")


# ─────────────────────────────────────────────────────────────────────────────
# analyze
# ─────────────────────────────────────────────────────────────────────────────
@app.command()
def analyze(
    citekey: Annotated[str, typer.Argument()],
    mode: Annotated[str, typer.Option("--mode")] = "critique",
) -> None:
    """Deep analysis of a paper (mode: critique)."""
    asyncio.run(_analyze(citekey.lstrip("@"), mode))


async def _analyze(citekey: str, mode: str) -> None:
    from sciassist.utils.logging import setup_logging
    from sciassist.router.model_router import ModelRouter
    from sciassist.utils.lm_studio_client import LMStudioClient
    from sciassist.note_generation.obsidian_builder import (
        _make_messages, _load_prompt, _render_prompt, _parse_json, _truncate
    )
    setup_logging()

    cfg = __import__("sciassist.config", fromlist=["get_settings"]).get_settings()
    md = cfg.raw_markdown_path / f"{citekey}.md"
    if not md.exists():
        console.print(f"[red]Markdown не найден. Запусти: sciassist process {citekey}[/red]")
        raise typer.Exit(1)

    full_text = _truncate(md.read_text(encoding="utf-8"), max_words=5000)
    prompt_text = _render_prompt(_load_prompt(mode), paper_text=full_text)

    router = ModelRouter()
    spec = router.select("reasoning")
    llm = LMStudioClient()

    console.print(f"[cyan]Анализ '{citekey}' (mode={mode})…[/cyan]")
    raw = await llm.chat(
        messages=_make_messages(prompt_text),
        model=spec.name, temperature=spec.temperature, timeout=spec.timeout,
    )

    result = _parse_json(raw)
    if result:
        for key, label in [
            ("strengths",           "✅ Сильные стороны"),
            ("weaknesses",          "❌ Слабые стороны"),
            ("missing_experiments", "🔬 Недостающие эксперименты"),
            ("overall",             "📋 Итог"),
        ]:
            val = result.get(key)
            if val:
                console.print(f"\n[bold]{label}[/bold]")
                if isinstance(val, list):
                    for v in val:
                        console.print(f"  • {v}")
                else:
                    console.print(f"  {val}")
    else:
        console.print(raw)


# ─────────────────────────────────────────────────────────────────────────────
# zotero subcommands
# ─────────────────────────────────────────────────────────────────────────────
zotero_app = typer.Typer(name="zotero", help="Zotero utilities.")
app.add_typer(zotero_app, name="zotero")


@zotero_app.command("list")
def zotero_list(
    collection: Annotated[str, typer.Option("--collection", "-c")] = "SciAssist Queue",
    check_pdf: Annotated[bool, typer.Option("--check-pdf")] = False,
) -> None:
    """List items in a Zotero collection."""
    from sciassist.utils.zotero_client import ZoteroClient, HTTPBackend
    zot = ZoteroClient()
    items = zot.get_items_in_collection(collection)
    if not items:
        console.print(f"[yellow]Коллекция '{collection}' пуста[/yellow]")
        return

    t = Table("Citekey", "Title", "Year", "PDF" if check_pdf else "",
              title=f"📚 {collection} ({len(items)})", show_edge=True)

    for item in items:
        pdf_status = ""
        if check_pdf and isinstance(zot.backend, HTTPBackend):
            pdf_status = "✓" if zot.get_pdf_path(item.key) else "✗"
        t.add_row(
            item.citekey or f"[dim]{item.key}[/dim]",
            item.title[:55] + "…" if len(item.title) > 55 else item.title,
            str(item.year or ""),
            pdf_status,
        )
    console.print(t)


@zotero_app.command("setup")
def zotero_setup() -> None:
    """Create SciAssist Queue and SciAssist Processed collections if missing."""
    from sciassist.utils.zotero_client import ZoteroClient
    zot = ZoteroClient()
    zot.ensure_collections()
    console.print("[green]✓ Коллекции проверены/созданы[/green]")


@zotero_app.command("status")
def zotero_status() -> None:
    """Show processing status for items in Queue and Processed."""
    from sciassist.utils.zotero_client import ZoteroClient
    from sciassist.pipeline.orchestrator import load_registry
    from sciassist.config import get_yaml_config

    zot = ZoteroClient()
    reg = load_registry()
    yaml_cfg = get_yaml_config()
    z_cfg = yaml_cfg.get("zotero", {})

    queue_name = z_cfg.get("queue_collection", "SciAssist Queue")
    proc_name  = z_cfg.get("processed_collection", "SciAssist Processed")

    queue_items = zot.get_items_in_collection(queue_name)
    proc_items  = zot.get_items_in_collection(proc_name)

    t = Table("Коллекция", "Статей")
    t.add_row(queue_name, str(len(queue_items)))
    t.add_row(proc_name,  str(len(proc_items)))
    t.add_row("В реестре (ok)",     str(sum(1 for v in reg.get("items", {}).values() if v["status"] == "ok")))
    t.add_row("В реестре (failed)", str(sum(1 for v in reg.get("items", {}).values() if v["status"] == "failed")))
    console.print(t)


if __name__ == "__main__":
    app()
