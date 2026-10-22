"""Разовое закрытие обработанных статей: registry mark_done + Zotero tag/move.
Причины: одиночный `process` не делает шаг 7 (Zotero-write есть только в queue-режиме),
реестра нет — `--queue` переработал бы готовые статьи заново (~2.5 ч LLM).
Идемпотентен: повторный запуск пропускает уже закрытые.
Run: python scripts/close_processed.py [citekey ...]   (без аргументов — известные 4)
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rich.console import Console

from sciassist.pipeline.orchestrator import load_registry, save_registry, mark_done
from sciassist.utils.zotero_client import ZoteroClient, HTTPBackend

console = Console()

DEFAULT = ["card2020little", "geiping2025scaling", "miller2024adding", "dror2018hitchhikers"]


async def main() -> None:
    cks = sys.argv[1:] or DEFAULT
    z = ZoteroClient()
    queue = {i.citekey: i for i in z.get_items_in_collection("SciAssist Queue") if i.citekey}
    reg = load_registry()
    for ck in cks:
        item = queue.get(ck)
        if item is None:
            console.print(f"[yellow]⚠ {ck}: не в Queue (уже перемещён?) — только registry[/]")
            key, found = None, False
            # найти в библиотеке по citekey
            for it in z.get_items_in_collection("SciAssist Queue") + z.get_items_in_collection("SciAssist Processed"):
                if it.citekey == ck:
                    key, found = it.key, True
                    break
        else:
            key, found = item.key, True
        if not found:
            console.print(f"[red]✗ {ck}: не найден в Zotero[/]")
            continue
        mark_done(reg, key, ck)
        if isinstance(z.backend, HTTPBackend) and key:
            try:
                z.add_tag(key, "processed")
                z.move_to_collection(key, "SciAssist Processed")
            except Exception as e:
                console.print(f"[yellow]⚠ {ck}: Zotero write упал ({e}) — registry всё равно закрыт[/]")
        console.print(f"[green]✓ {ck}: registry + тег + Processed[/]")
    save_registry(reg)
    console.print("[bold]реестр сохранён[/]")


if __name__ == "__main__":
    asyncio.run(main())
