"""
SciAssist Health Check — проверяет всю инфраструктуру.
Run: python scripts/healthcheck.py
"""
import asyncio
import sys
from pathlib import Path

# Ensure src/ is importable when run as a script
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import httpx
from rich.console import Console
from rich.rule import Rule
from rich.table import Table

console = Console()


def row(label: str, ok: bool, detail: str = "") -> None:
    icon = "[bold green]✅[/bold green]" if ok else "[bold red]❌[/bold red]"
    detail_str = f"  [dim]{detail}[/dim]" if detail else ""
    console.print(f" {icon}  {label}{detail_str}")


async def run() -> int:
    """Returns count of critical failures."""
    from sciassist.config import get_settings, get_yaml_config
    from sciassist.utils.logging import setup_logging

    setup_logging(debug=False)
    cfg = get_settings()
    failures = 0

    console.print(Rule("[bold cyan]SciAssist Health Check[/bold cyan]"))

    # ── 1. LM Studio ──────────────────────────────────────────────────
    console.print("\n[bold]LM Studio[/bold]")
    models: list[str] = []
    try:
        from sciassist.utils.lm_studio_client import LMStudioClient
        client = LMStudioClient()
        models = await client.list_loaded_models()
        row("API доступен", True, f"{len(models)} моделей загружено")
        if models:
            for m in models:
                console.print(f"   [dim]• {m}[/dim]")
    except Exception as e:
        row("API доступен", False, str(e)[:100])
        failures += 1

    # ── 2. Embed test ─────────────────────────────────────────────────
    if models:
        try:
            from sciassist.utils.lm_studio_client import LMStudioClient
            client = LMStudioClient()
            yaml_cfg = get_yaml_config()
            embed_model = yaml_cfg.get("rag", {}).get("embedding_model", "text-embedding-bge-m3")
            vecs = await client.embed(["test embedding"], model=embed_model)
            dim = len(vecs[0]) if vecs else 0
            ok = dim > 0
            row(f"Embed ({embed_model})", ok, f"dim={dim}")
            if not ok:
                failures += 1
        except Exception as e:
            row("Embed", False, str(e)[:80])
            failures += 1
    else:
        row("Embed", False, "LM Studio недоступен — пропуск")
        failures += 1

    # ── 3. Zotero HTTP API ────────────────────────────────────────────
    console.print("\n[bold]Zotero[/bold]")
    zotero_http_ok = False
    try:
        r = httpx.get(f"{cfg.zotero_local_url}/api/users/0/items?limit=1", timeout=3.0)
        zotero_http_ok = r.status_code == 200
        row("HTTP API (pyzotero local)", zotero_http_ok, f"HTTP {r.status_code}")
        if not zotero_http_ok:
            failures += 1
    except Exception:
        row("HTTP API", False, "Zotero не запущен — открой Zotero")
        failures += 1

    # ── 4. Zotero SQLite ──────────────────────────────────────────────
    sqlite_path = cfg.zotero_data_dir / "zotero.sqlite"
    row("SQLite fallback", sqlite_path.exists(), str(sqlite_path))
    if not sqlite_path.exists():
        failures += 1

    # ── 5. BBT JSON-RPC ───────────────────────────────────────────────
    bbt_url = f"{cfg.zotero_local_url}/better-bibtex/json-rpc"
    try:
        r = httpx.post(
            bbt_url,
            json={"jsonrpc": "2.0", "method": "item.search", "params": ["x"], "id": 1},
            timeout=3.0,
        )
        row("BBT JSON-RPC", r.status_code in (200, 400), f"HTTP {r.status_code}")
    except Exception:
        row("BBT JSON-RPC", False, "BBT недоступен (опционально)")
        # Not a hard failure

    # ── 6. Zotero Collections ─────────────────────────────────────────
    if zotero_http_ok:
        try:
            from sciassist.utils.zotero_client import ZoteroClient
            zot = ZoteroClient()
            yaml_cfg = get_yaml_config()
            z_cfg = yaml_cfg.get("zotero", {})
            queue = z_cfg.get("queue_collection", "SciAssist Queue")
            processed = z_cfg.get("processed_collection", "SciAssist Processed")
            existing = zot.get_collection_names()

            for col in [queue, processed]:
                found = col in existing
                if not found:
                    console.print(f" [yellow]⚠️[/yellow]   Коллекция '{col}' не найдена")
                    ans = console.input(f"    Создать '{col}'? [y/n]: ").strip().lower()
                    if ans == "y":
                        from sciassist.utils.zotero_client import HTTPBackend
                        if isinstance(zot.backend, HTTPBackend):
                            zot.backend.create_collection(col)
                            row(f"Коллекция '{col}'", True, "создана")
                    else:
                        failures += 1
                else:
                    row(f"Коллекция '{col}'", True)
        except Exception as e:
            row("Zotero коллекции", False, str(e)[:80])
            failures += 1

    # ── 7. Obsidian Vault ─────────────────────────────────────────────
    console.print("\n[bold]Файловая система[/bold]")
    vault = cfg.obsidian_vault
    try:
        probe = vault / ".sciassist_probe"
        probe.write_text("ok")
        probe.unlink()
        row("Obsidian Vault", True, str(vault))
    except Exception as e:
        row("Obsidian Vault", False, str(e)[:80])
        failures += 1

    # ── 8. ChromaDB ───────────────────────────────────────────────────
    try:
        import chromadb
        db = chromadb.PersistentClient(path=str(cfg.chroma_db_path))
        cols = db.list_collections()
        row("ChromaDB", True, f"{len(cols)} коллекций, путь: {cfg.chroma_db_path}")
    except Exception as e:
        row("ChromaDB", False, str(e)[:80])
        failures += 1

    # ── 9. Data directories ───────────────────────────────────────────
    for d in [cfg.raw_markdown_path, cfg.extracted_figures_path, cfg.logs_path]:
        d.mkdir(parents=True, exist_ok=True)
    row("Data директории", True, "raw_markdown / extracted_figures / logs")

    # ── Summary ───────────────────────────────────────────────────────
    console.print()
    console.print(Rule())
    if failures == 0:
        console.print("[bold green]✅  Все проверки пройдены — SciAssist готов к работе![/bold green]")
    else:
        console.print(f"[bold red]❌  {failures} проверок не прошло. Исправь ошибки выше.[/bold red]")

    return failures


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
