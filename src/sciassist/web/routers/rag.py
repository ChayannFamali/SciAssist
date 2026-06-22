"""RAG endpoints — search / ask / similar."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel

from sciassist.exceptions import LMStudioError, ZoteroBackendError
from sciassist.web.deps import (
    LLM_SEMAPHORE,
    get_query_engine,
    get_rag_indexer,
    get_app_settings,
    run_sync,
)

router = APIRouter()

_COLLECTION = Literal["papers_full", "papers_notes", "both"]


# ─────────────────────────────────────────────────────────────────────────────
# /api/search
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/api/search")
async def search(
    q: Annotated[str, Query(min_length=1, description="Поисковый запрос")],
    top: Annotated[int, Query(ge=1, le=50)] = 5,
    col: Annotated[str, Query(description="papers_full | papers_notes")] = "papers_full",
) -> list[dict]:
    """Семантический поиск — сырые чанки, без LLM-генерации."""
    if col not in ("papers_full", "papers_notes"):
        raise HTTPException(status_code=400, detail=f"Unknown collection: {col}")

    try:
        async with LLM_SEMAPHORE:
            raw = await get_rag_indexer().query(q, top_k=top, collection=col)
    except LMStudioError as e:
        raise HTTPException(status_code=503, detail=str(e))

    docs = raw.get("documents", [[]])[0]
    metas = raw.get("metadatas", [[]])[0]
    dists = raw.get("distances", [[]])[0]

    out: list[dict] = []
    for doc, meta, dist in zip(docs, metas, dists):
        score = round(1 - float(dist), 3)
        preview = doc[:100].replace("\n", " ").strip()
        out.append({
            "citekey": meta.get("citekey", ""),
            "section": meta.get("section", ""),
            "score": score,
            "preview": preview + ("…" if len(doc) > 100 else ""),
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# /api/ask
# ─────────────────────────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str
    top_k: int = 5
    min_score: float = 0.4
    max_per_paper: int = 3
    rerank: bool = True
    hybrid: bool = True
    hyde: bool = False
    collection: _COLLECTION = "papers_full"


@router.post("/api/ask")
async def ask(req: AskRequest) -> dict:
    """RAG-вопрос с цитатами [citekey]. 1:1 с CLI `ask`.

    Вызов QueryEngine ПОД общим LLM-семафором —
    параллельные запросы в LM Studio не идут одновременно.
    """
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question is empty")

    try:
        async with LLM_SEMAPHORE:
            result = await get_query_engine().ask(
                req.question,
                top_k=req.top_k,
                min_score=req.min_score,
                max_per_paper=req.max_per_paper,
                rerank=req.rerank,
                hybrid=req.hybrid,
                hyde=req.hyde,
                collection=req.collection,
            )
    except LMStudioError as e:
        raise HTTPException(status_code=503, detail=str(e))

    # RAGAnswer — pydantic, сериализуется как JSON с .answer/.sources/.model
    return result.model_dump()


# ─────────────────────────────────────────────────────────────────────────────
# /api/similar/{citekey}
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/api/similar/{citekey}")
async def similar(
    citekey: Annotated[str, Path(min_length=1, description="Citekey (с/без @)")],
    top: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[dict]:
    """Статьи, похожие на @citekey (по эмбеддингу первых ~600 слов markdown)."""
    ck = citekey.lstrip("@")
    cfg = get_app_settings()
    md_path = cfg.raw_markdown_path / f"{ck}.md"

    def _read_query() -> str:
        if not md_path.exists():
            return ""
        return " ".join(md_path.read_text(encoding="utf-8").split()[:600])

    query = await run_sync(_read_query)
    if not query:
        raise HTTPException(
            status_code=404,
            detail=f"Markdown не найден. Сначала: sciassist process {ck}",
        )

    try:
        async with LLM_SEMAPHORE:
            raw = await get_rag_indexer().query(query, top_k=top * 4)
    except LMStudioError as e:
        raise HTTPException(status_code=503, detail=str(e))

    metas = raw.get("metadatas", [[]])[0]
    dists = raw.get("distances", [[]])[0]

    seen: set[str] = {ck}
    out: list[dict] = []
    for meta, dist in zip(metas, dists):
        c = meta.get("citekey", "")
        if not c or c in seen:
            continue
        seen.add(c)
        out.append({
            "citekey": c,
            "section": meta.get("section", ""),
            "score": round(1 - float(dist), 3),
        })
        if len(out) >= top:
            break

    return out