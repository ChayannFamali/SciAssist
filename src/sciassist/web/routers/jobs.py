"""Jobs API — POST для запуска + GET для опроса прогресса."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field

from sciassist.web.job_manager import get_job_manager
from sciassist.web.services import (
    analyze,
    draft_related_work,
    gaps,
    process_paper,
    process_queue,
)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Request schemas
# ─────────────────────────────────────────────────────────────────────────────
class ProcessRequest(BaseModel):
    citekey: str
    only: Literal["markdown", "full"] = "full"
    force: bool = False


class GapsRequest(BaseModel):
    topic: str
    papers: int = Field(default=10, ge=1, le=50)


class DraftRequest(BaseModel):
    topic: str
    papers: int = Field(default=10, ge=1, le=50)


class AnalyzeRequest(BaseModel):
    citekey: str
    mode: Literal["critique"] = "critique"


# ─────────────────────────────────────────────────────────────────────────────
# POST: запуск джоб
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/api/process")
async def post_process(req: ProcessRequest) -> dict:
    if not req.citekey.strip():
        raise HTTPException(status_code=400, detail="citekey is empty")
    jm = get_job_manager()
    job_id = await jm.submit(
        "process", process_paper,
        citekey=req.citekey.lstrip("@"),
        only=req.only,
        force=req.force,
    )
    return {"job_id": job_id, "status": "queued", "kind": "process"}


@router.post("/api/process-queue")
async def post_process_queue() -> dict:
    jm = get_job_manager()
    job_id = await jm.submit("process_queue", process_queue)
    return {"job_id": job_id, "status": "queued", "kind": "process_queue"}


@router.post("/api/gaps")
async def post_gaps(req: GapsRequest) -> dict:
    if not req.topic.strip():
        raise HTTPException(status_code=400, detail="topic is empty")
    jm = get_job_manager()
    job_id = await jm.submit(
        "gaps", gaps,
        topic=req.topic,
        papers=req.papers,
    )
    return {"job_id": job_id, "status": "queued", "kind": "gaps"}


@router.post("/api/draft/related-work")
async def post_draft(req: DraftRequest) -> dict:
    if not req.topic.strip():
        raise HTTPException(status_code=400, detail="topic is empty")
    jm = get_job_manager()
    job_id = await jm.submit(
        "draft", draft_related_work,
        topic=req.topic,
        papers=req.papers,
    )
    return {"job_id": job_id, "status": "queued", "kind": "draft"}


@router.post("/api/analyze")
async def post_analyze(req: AnalyzeRequest) -> dict:
    if not req.citekey.strip():
        raise HTTPException(status_code=400, detail="citekey is empty")
    jm = get_job_manager()
    job_id = await jm.submit(
        "analyze", analyze,
        citekey=req.citekey.lstrip("@"),
        mode=req.mode,
    )
    return {"job_id": job_id, "status": "queued", "kind": "analyze"}


# ─────────────────────────────────────────────────────────────────────────────
# GET: опрос статуса
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/api/jobs/{job_id}")
async def get_job(
    job_id: Annotated[str, Path(min_length=1)],
) -> dict:
    jm = get_job_manager()
    job = jm.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return job.to_dict()


@router.get("/api/jobs")
async def list_jobs(
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> dict:
    jm = get_job_manager()
    return {"jobs": [j.to_dict() for j in jm.list(limit=limit)]}


@router.delete("/api/jobs/{job_id}")
async def cancel_job(
    job_id: Annotated[str, Path(min_length=1)],
) -> dict:
    jm = get_job_manager()
    if not jm.cancel(job_id):
        raise HTTPException(status_code=404, detail=f"job {job_id} not found or already done")
    return {"job_id": job_id, "status": "cancelled"}