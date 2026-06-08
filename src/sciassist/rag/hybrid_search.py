"""Sparse BM25 retrieval over a ChromaDB collection (in-memory, lazy)."""
import re

from loguru import logger
from rank_bm25 import BM25Okapi

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


class BM25Index:
    """Строит BM25 поверх всех чанков коллекции. Ленивая сборка, кэш в памяти."""

    def __init__(self, collection) -> None:
        self._col = collection
        self._bm25: BM25Okapi | None = None
        self._ids: list[str] = []
        self._docs: list[str] = []
        self._metas: list[dict] = []

    def _build(self) -> None:
        data = self._col.get(include=["documents", "metadatas"])
        self._ids = data.get("ids", []) or []
        self._docs = data.get("documents", []) or []
        self._metas = data.get("metadatas", []) or []
        corpus = [_tokenize(d) for d in self._docs]
        self._bm25 = BM25Okapi(corpus) if corpus else None
        logger.debug(f"BM25: индекс построен ({len(self._docs)} чанков)")

    def search(self, query: str, top_k: int) -> list[tuple[str, str, dict, float]]:
        """Вернуть [(id, doc, meta, bm25_score)], только score > 0."""
        if self._bm25 is None:
            self._build()
        if not self._bm25:
            return []
        scores = self._bm25.get_scores(_tokenize(query))
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        out = []
        for i in order[:top_k]:
            if scores[i] <= 0:
                continue
            out.append((self._ids[i], self._docs[i], self._metas[i], float(scores[i])))
        return out
