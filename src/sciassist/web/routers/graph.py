"""Graph API — три режима: links / semantic / overlay."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from sciassist.web.deps import run_sync
from sciassist.web.graph_builder import build_graph

router = APIRouter()


@router.get("/api/graph")
async def graph(
    mode: Annotated[Literal["links", "semantic", "overlay"], Query()] = "overlay",
    threshold: Annotated[float, Query(ge=0.0, le=1.0)] = 0.55,
    top_k: Annotated[int, Query(ge=1, le=50)] = 5,
    refresh: Annotated[int, Query(ge=0, le=1, description="1 = пересчитать семантику")] = 0,
) -> dict:
    """Граф статей: узлы + рёбра.

    • mode=links    — только [[wiki]] рёбра
    • mode=semantic — только смысловые (по эмбеддингам)
    • mode=overlay  — объединение (по умолчанию)
    """
    return await run_sync(
        build_graph,
        mode=mode,
        threshold=threshold,
        top_k=top_k,
        refresh=bool(refresh),
    )