"""Singletons + LLM semaphore for SciAssist Web.

Импортирует ядро, создаёт по одному экземпляру каждого движка
(lru_cache), плюс общий asyncio.Semaphore для всех LLM-вызовов
(LM Studio однопоточный).

Sync-код (ZoteroClient) вызывается через `run_sync()` →
`asyncio.to_thread`, чтобы не блокировать event loop.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any, TypeVar

from sciassist.config import get_settings
from sciassist.indexing.rag_indexer import RAGIndexer
from sciassist.rag.query_engine import QueryEngine
from sciassist.utils.lm_studio_client import LMStudioClient
from sciassist.utils.zotero_client import ZoteroClient

# Один семафор на все LLM-вызовы: LM Studio обслуживает один запрос за раз.
LLM_SEMAPHORE: asyncio.Semaphore = asyncio.Semaphore(1)

T = TypeVar("T")


@lru_cache(maxsize=1)
def get_query_engine() -> QueryEngine:
    """Ленивый синглтон QueryEngine (RAG)."""
    return QueryEngine()


@lru_cache(maxsize=1)
def get_rag_indexer() -> RAGIndexer:
    """Ленивый синглтон RAGIndexer (ChromaDB)."""
    return RAGIndexer()


@lru_cache(maxsize=1)
def get_lm_studio() -> LMStudioClient:
    """Ленивый синглтон LMStudioClient."""
    return LMStudioClient()


@lru_cache(maxsize=1)
def get_zotero() -> ZoteroClient:
    """Ленивый синглтон ZoteroClient.

    Может кинуть ZoteroBackendError если оба бэкенда недоступны —
    lru_cache не кэширует исключения, поэтому следующий вызов попробует снова.
    """
    return ZoteroClient()


def get_app_settings():
    """Получить настройки (через lru_cache в sciassist.config)."""
    return get_settings()


async def run_sync(func: Any, *args: Any, **kwargs: Any) -> T:
    """Запустить блокирующую функцию в threadpool (off event loop).

    Использовать для ZoteroClient и любого sync IO/вычислений.
    """
    return await asyncio.to_thread(func, *args, **kwargs)


__all__ = [
    "LLM_SEMAPHORE",
    "get_query_engine",
    "get_rag_indexer",
    "get_lm_studio",
    "get_zotero",
    "get_app_settings",
    "run_sync",
]