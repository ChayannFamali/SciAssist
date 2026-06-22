"""Library / health / logs / zotero-list — read-only endpoints."""
from __future__ import annotations

import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from sciassist.exceptions import ZoteroBackendError
from sciassist.web.deps import (
    get_lm_studio,
    get_rag_indexer,
    get_zotero,
    run_sync,
)

router = APIRouter()

# Health-проверки жёстко ограничены по времени — UI пингует /api/health
# каждые 10с, и подвисшая LM Studio не должна ронять интерфейс.
# LM Studio retry внутри клиента делает 2с+4с — поэтому таймаут 1.5с
# (если LM Studio недоступна, мы НЕ дожидаемся её retry — UI увидит null).
_LM_TIMEOUT = 1.5
_ZOTERO_TIMEOUT = 1.0
_CHROMA_TIMEOUT = 1.0


# ─────────────────────────────────────────────────────────────────────────────
# /api/health
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/api/health")
async def health() -> dict:
    """Статус сервисов: LM Studio, Zotero, ChromaDB.

    Ни один источник не должен ронять ручку — возвращаем bool/null.
    Каждая проверка ограничена _HEALTH_TIMEOUT секундами.
    """
    # LM Studio — async, может зависнуть на retry — ограничиваем
    lm_ok: bool | None = None
    try:
        models = await asyncio.wait_for(
            get_lm_studio().list_loaded_models(), timeout=_LM_TIMEOUT
        )
        lm_ok = len(models) > 0
    except Exception:
        lm_ok = None  # не успели — показываем «неизвестно»

    # Zotero — sync, через to_thread; init может кинуть ZoteroBackendError
    zotero_status: dict = {"ok": None, "backend": None, "write": False}
    try:
        result = await asyncio.wait_for(
            run_sync(get_zotero().health_check), timeout=_ZOTERO_TIMEOUT
        )
        zotero_status = {
            "ok": bool(result.get("http") or result.get("sqlite")),
            "backend": result.get("backend"),
            "write": bool(result.get("write")),
        }
    except (ZoteroBackendError, asyncio.TimeoutError):
        pass  # ok остаётся None
    except Exception:
        pass

    # ChromaDB — sync, stats() возвращает dict
    chroma_ok: bool | None = None
    try:
        s = await asyncio.wait_for(
            run_sync(get_rag_indexer().stats), timeout=_CHROMA_TIMEOUT
        )
        chroma_ok = s.get("papers_full_chunks", 0) > 0 or s.get("papers_notes_chunks", 0) > 0
    except Exception:
        chroma_ok = None

    return {
        "lm_studio": lm_ok,
        "zotero": zotero_status,
        "chroma": chroma_ok,
    }


# ─────────────────────────────────────────────────────────────────────────────
# /api/stats
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/api/stats")
async def stats() -> dict:
    """Статистика индекса (RAGIndexer.stats)."""
    try:
        return get_rag_indexer().stats()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"ChromaDB недоступна: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# /api/logs
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/api/logs")
async def logs(
    tail: Annotated[int, Query(ge=1, le=1000)] = 20,
) -> list[dict]:
    """Последние N LLM-вызовов из llm_calls.jsonl."""
    cfg = __import__("sciassist.config", fromlist=["get_settings"]).get_settings()
    log_file = cfg.logs_path / "llm_calls.jsonl"

    if not log_file.exists():
        return []

    def _read() -> list[dict]:
        out: list[dict] = []
        for line in log_file.read_text(encoding="utf-8").splitlines()[-tail:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    return await run_sync(_read)


# ─────────────────────────────────────────────────────────────────────────────
# /api/zotero/list
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/api/zotero/list")
async def zotero_list(
    collection: Annotated[str, Query(description="Имя коллекции в Zotero")] = "SciAssist Queue",
) -> list[dict]:
    """Список статей в коллекции: [{citekey, title, year}]."""
    def _fetch() -> list[dict]:
        items = get_zotero().get_items_in_collection(collection)
        return [
            {
                "citekey": it.citekey or it.key,
                "title": it.title,
                "year": it.year,
            }
            for it in items
        ]

    try:
        return await run_sync(_fetch)
    except ZoteroBackendError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Zotero недоступен: {e}")