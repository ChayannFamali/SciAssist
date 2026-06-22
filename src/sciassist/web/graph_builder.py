"""Граф статей для веб-морды.

Узлы — статьи (.md в vault/papers/). Концепты/датасеты НЕ включаются.

Рёбра:
  • link     — структурные, из [[wiki-ссылок]] (только между статьями)
  • semantic — смысловые, по косинусу усреднённых эмбеддингов из ChromaDB

Семантика КЭШИРУЕТСЯ в памяти. Инвалидация по `refresh=True` или
изменению коллекции `papers_notes` (по счётчику).
"""
from __future__ import annotations

import re
import threading
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import frontmatter

from sciassist.config import get_settings
from sciassist.exceptions import LMStudioError
from sciassist.web.deps import LLM_SEMAPHORE, get_rag_indexer

_LINK_RE = re.compile(r"\[\[([^\]\n]+?)\]\]")

# Концепты и датасеты живут в других папках vault — отфильтруем их,
# чтобы не тянуть в граф как узлы-статьи.
_IGNORED_FOLDERS = {"concepts", "datasets"}


@dataclass
class _SemanticCache:
    edges: list[dict]
    coll_count: int
    embed_model: str


_cache: _SemanticCache | None = None
_cache_lock = threading.Lock()


def _node_id(path: Path) -> str:
    """id узла = имя файла без @ и .md."""
    return path.stem.lstrip("@")


def _scan_papers() -> tuple[list[Path], dict[str, dict]]:
    """Скан vault/papers/. Возвращает (список путей, {id → node_meta})."""
    cfg = get_settings()
    folder = cfg.obsidian_vault / cfg.obsidian_papers_folder
    if not folder.exists():
        return [], {}

    paths: list[Path] = []
    nodes: dict[str, dict] = {}

    for p in sorted(folder.glob("*.md")):
        cid = _node_id(p)
        paths.append(p)

        # frontmatter: tags, year
        tags: list[str] = []
        year: int | None = None
        try:
            post = frontmatter.loads(p.read_text(encoding="utf-8"))
            md = dict(post.metadata)
            tags = md.get("tags", []) or []
            year = md.get("year")
        except Exception:
            pass

        nodes[cid] = {
            "id": cid,
            "label": cid,
            "title": tags and None,  # заполним ниже если есть
            "year": year,
            "tags": tags,
            "degree": 0,
        }

    # title = первый H1 (если есть)
    for p in paths:
        cid = _node_id(p)
        try:
            text = p.read_text(encoding="utf-8")
            m = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
            if m:
                nodes[cid]["title"] = m.group(1).strip()
        except Exception:
            pass

    return paths, nodes


def _structural_edges(paths: list[Path], nodes: dict[str, dict]) -> list[dict]:
    """Рёбра из [[wiki-ссылок]] между узлами-статьями. Дедуп, без петель."""
    ids = set(nodes.keys())
    edge_count: dict[tuple[str, str], int] = defaultdict(int)

    for p in paths:
        src = _node_id(p)
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue

        for raw in _LINK_RE.findall(text):
            target = raw.split("|", 1)[0].split("#", 1)[0].strip().lstrip("@")
            if not target or target not in ids or target == src:
                continue
            edge_count[(src, target)] += 1

    edges = []
    for (s, t), _ in edge_count.items():
        edges.append({"source": s, "target": t, "kind": "link"})
        nodes[s]["degree"] += 1
        nodes[t]["degree"] += 1

    return edges


def _collection_signature() -> tuple[int, str]:
    """Счётчик чанков + модель — для инвалидации кэша."""
    idx = get_rag_indexer()
    s = idx.stats()
    return (s.get("papers_notes_chunks", 0), s.get("embed_model", ""))


def _build_semantic_edges(top_k: int, threshold: float) -> list[dict]:
    """Семантические рёбра: средний эмбеддинг на статью → top-K ближайших."""
    idx = get_rag_indexer()
    col = idx.get_collection("papers_notes")

    data = col.get(include=["embeddings", "metadatas"])
    ids = data.get("ids") or []
    embs_raw = data.get("embeddings")
    embs = list(embs_raw) if embs_raw is not None else []
    metas = data.get("metadatas") or []

    if not embs:
        return []

    # Группируем эмбеддинги по citekey → среднее
    groups: dict[str, list[list[float]]] = defaultdict(list)
    titles: dict[str, str] = {}
    for cid, vec, meta in zip(ids, embs, metas):
        ck = (meta or {}).get("citekey", "")
        if not ck:
            continue
        groups[ck].append(vec)

    keys = sorted(groups.keys())
    if not keys:
        return []

    import math

    def _cos(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1e-12
        nb = math.sqrt(sum(y * y for y in b)) or 1e-12
        return dot / (na * nb)

    # Средние векторы
    mean_vecs: dict[str, list[float]] = {}
    dim = len(next(iter(groups.values()))[0])
    for ck in keys:
        vecs = groups[ck]
        n = len(vecs)
        acc = [0.0] * dim
        for v in vecs:
            for i in range(dim):
                acc[i] += v[i]
        mean_vecs[ck] = [x / n for x in acc]

    edges: list[dict] = []
    for ck in keys:
        scored = []
        for other in keys:
            if other == ck:
                continue
            score = _cos(mean_vecs[ck], mean_vecs[other])
            if score >= threshold:
                scored.append((other, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        for other, score in scored[:top_k]:
            a, b = sorted([ck, other])
            edges.append({"source": a, "target": b, "kind": "semantic", "score": round(score, 3)})

    # Дедуп симметричных пар (оставляем с большим score при коллизии)
    seen: dict[tuple[str, str], dict] = {}
    for e in edges:
        k = (e["source"], e["target"])
        if k not in seen or e["score"] > seen[k]["score"]:
            seen[k] = e
    return list(seen.values())


def _semantic_edges_cached(top_k: int, threshold: float) -> list[dict]:
    """Кэшированная версия: пересчёт только при инвалидации."""
    global _cache
    sig = _collection_signature()

    with _cache_lock:
        if _cache is not None and (_cache.coll_count, _cache.embed_model) == sig:
            # кэш валиден — отфильтровать по параметрам
            return [
                e for e in _cache.edges
                if e.get("score", 1.0) >= threshold
            ][:0] or _filter_semantic(_cache.edges, top_k, threshold)

        try:
            edges = _build_semantic_edges(top_k, threshold)
        except LMStudioError:
            # LM Studio недоступна — отдаём пустой список
            edges = []

        _cache = _SemanticCache(edges=edges, coll_count=sig[0], embed_model=sig[1])
        return edges


def _filter_semantic(edges: list[dict], top_k: int, threshold: float) -> list[dict]:
    """Перефильтровать закэшированные рёбра по top_k/threshold на узел."""
    per_node: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        per_node[e["source"]].append(e)
        per_node[e["target"]].append(e)

    out: dict[tuple[str, str], dict] = {}
    for e in edges:
        if e.get("score", 1.0) < threshold:
            continue
        out[(e["source"], e["target"])] = e

    # Ограничение по top_k на узел (на каждое направление)
    count: dict[str, int] = defaultdict(int)
    limited: list[dict] = []
    for e in out.values():
        s, t = e["source"], e["target"]
        if count[s] >= top_k and count[t] >= top_k:
            continue
        limited.append(e)
        count[s] += 1
        count[t] += 1
    return limited


def build_graph(
    *,
    mode: str = "links",
    threshold: float = 0.55,
    top_k: int = 5,
    refresh: bool = False,
) -> dict:
    """Построить граф статей.

    mode:
      • links    — только структурные рёбра
      • semantic — только семантические
      • overlay  — объединение
    """
    paths, nodes = _scan_papers()
    link_edges = _structural_edges(paths, nodes)

    sem_edges: list[dict] = []
    if mode in ("semantic", "overlay"):
        if refresh:
            global _cache
            with _cache_lock:
                _cache = None
        sem_edges = _semantic_edges_cached(top_k, threshold)
        # Учёт degree для семантических рёбер тоже
        for e in sem_edges:
            nodes[e["source"]]["degree"] += 1
            nodes[e["target"]]["degree"] += 1

    if mode == "links":
        edges = link_edges
    elif mode == "semantic":
        edges = sem_edges
    else:
        edges = link_edges + sem_edges

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "mode": mode,
    }


__all__ = ["build_graph"]