"""Queue orchestrator — processes all items in 'SciAssist Queue'."""
import json
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from rich.console import Console
from rich.rule import Rule

from sciassist.config import get_settings, get_yaml_config
from sciassist.exceptions import PDFProcessingError
from sciassist.indexing.rag_indexer import RAGIndexer
from sciassist.note_generation.obsidian_builder import build_note
from sciassist.preprocessing.pdf_to_markdown import process_pdf
from sciassist.utils.zotero_client import HTTPBackend, ZoteroClient
from sciassist.vision.figure_extractor import extract_figures

console = Console()


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

def _reg_path() -> Path:
    return get_settings().registry_path


def load_registry() -> dict:
    p = _reg_path()
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"version": 1, "items": {}}


def save_registry(reg: dict) -> None:
    p = _reg_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(reg, indent=2, ensure_ascii=False), encoding="utf-8")


def mark_done(reg: dict, key: str, citekey: str) -> None:
    reg.setdefault("items", {})[key] = {
        "citekey": citekey,
        "status": "ok",
        "done_at": datetime.now(timezone.utc).isoformat(),
        "error": None,
    }


def mark_failed(reg: dict, key: str, citekey: str, error: str) -> None:
    reg.setdefault("items", {})[key] = {
        "citekey": citekey,
        "status": "failed",
        "done_at": datetime.now(timezone.utc).isoformat(),
        "error": error[:500],
    }


def is_done(reg: dict, key: str) -> bool:
    return reg.get("items", {}).get(key, {}).get("status") == "ok"


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def process_queue(force: bool = False) -> dict:
    """
    Process all items in 'SciAssist Queue'.
    Returns summary: {total, processed, skipped, failed}.
    """
    zot = ZoteroClient()
    yaml_cfg = get_yaml_config()
    z_cfg = yaml_cfg.get("zotero", {})

    queue_name     = z_cfg.get("queue_collection",     "SciAssist Queue")
    processed_name = z_cfg.get("processed_collection", "SciAssist Processed")
    skip_tag       = z_cfg.get("skip_tag",             "skip_sciassist")
    processed_tag  = z_cfg.get("processed_tag",        "processed")

    items = zot.get_items_in_collection(queue_name)
    if not items:
        console.print(f"[yellow]Коллекция '{queue_name}' пуста[/yellow]")
        return {"total": 0, "processed": 0, "skipped": 0, "failed": 0}

    registry = load_registry()

    # Split into to_process / skip
    to_process = []
    skipped = 0
    for item in items:
        if skip_tag in item.tags:
            logger.debug(f"Skip {item.citekey}: тег {skip_tag}")
            skipped += 1
            continue
        if not force and is_done(registry, item.key):
            logger.debug(f"Skip {item.citekey}: уже обработана")
            skipped += 1
            continue
        if not item.citekey:
            logger.warning(f"Skip {item.key}: нет citekey")
            skipped += 1
            continue
        to_process.append(item)

    console.print(
        f"[cyan]Очередь:[/cyan] {len(to_process)} к обработке, "
        f"{skipped} пропущено, {len(items)} всего"
    )

    if not to_process:
        console.print("[green]Нечего делать.[/green]")
        return {"total": len(items), "processed": 0, "skipped": skipped, "failed": 0}

    indexer = RAGIndexer()
    done_count = 0
    fail_count = 0

    for i, item in enumerate(to_process, 1):
        console.print(Rule(f"[bold]{i}/{len(to_process)} — {item.citekey}[/bold]"))

        try:
            # 1. PDF
            pdf = zot.get_pdf_path(item.key)
            if pdf is None:
                raise PDFProcessingError(f"PDF не найден для {item.key}")

            meta = {
                "title": item.title,
                "year": item.year,
                "doi": item.doi,
                "authors": [{"first": a.first, "last": a.last} for a in item.authors],
            }

            # 2. OCR → Markdown
            console.print("[dim]→ OCR…[/dim]")
            md_path = await process_pdf(pdf, item.citekey, meta)

            # 3. Figures
            console.print("[dim]→ Фигуры…[/dim]")
            await extract_figures(pdf, item.citekey)

            # 4. Index paper
            await indexer.index_paper(item.citekey, md_path, force=force)

            # 5. Obsidian note
            console.print("[dim]→ Заметка…[/dim]")
            note_path = await build_note(item.citekey, item, force=force)

            # 6. Index note
            await indexer.index_note(item.citekey, note_path, force=force)

            # 7. Zotero: tag + move (best-effort, не останавливаем при ошибке)
            if isinstance(zot.backend, HTTPBackend):
                try:
                    zot.add_tag(item.key, processed_tag)
                    zot.move_to_collection(item.key, processed_name)
                except Exception as e:
                    logger.warning(
                        f"{item.citekey}: Zotero write недоступен (local API не поддерживает PATCH) — "
                        f"тег/перемещение пропущено. Статья обработана корректно."
                    )

            mark_done(registry, item.key, item.citekey)
            save_registry(registry)   # коммит после каждой статьи
            done_count += 1
            console.print(f"[green]✓ {item.citekey}[/green]")

        except Exception as e:
            logger.exception(f"Ошибка при обработке {item.citekey}")
            mark_failed(registry, item.key, item.citekey, str(e))
            save_registry(registry)
            fail_count += 1
            console.print(f"[red]✗ {item.citekey}: {e}[/red]")

    summary = {
        "total": len(items),
        "processed": done_count,
        "skipped": skipped,
        "failed": fail_count,
    }
    console.print(Rule())
    console.print(
        f"[bold]Итого:[/bold] ✓ {done_count} обработано, "
        f"✗ {fail_count} ошибок, ⏭ {skipped} пропущено"
    )
    return summary
