"""SciAssist Web — FastAPI application.

Тонкая обёртка над async-ядром. Никакой бизнес-логики здесь:
всё через импорт ядра и deps.
"""
from __future__ import annotations

import traceback

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from sciassist.config import get_settings
from sciassist.web.routers import library as library_router
from sciassist.web.routers import rag as rag_router
from sciassist.web.routers import notes as notes_router
from sciassist.web.routers import graph as graph_router
from sciassist.web.routers import jobs as jobs_router

app = FastAPI(title="SciAssist Web", version="0.1.0")


# ─────────────────────────────────────────────────────────────────────────────
# Единый формат ошибок (TX.1)
# ─────────────────────────────────────────────────────────────────────────────
# Все ошибки возвращаются как {"error": {"code": <int>, "type": <str>,
#   "message": <str>, "path": <str>}}. Никаких traceback наружу.
def _err(code: int, type_: str, message: str, path: str) -> JSONResponse:
    return JSONResponse(
        status_code=code,
        content={"error": {"code": code, "type": type_,
                            "message": message, "path": path}},
    )


@app.exception_handler(HTTPException)
async def _http_exc_handler(request: Request, exc: HTTPException) -> JSONResponse:
    # Маппинг detail → message
    msg = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    type_ = {
        400: "bad_request",
        404: "not_found",
        409: "conflict",
        503: "service_unavailable",
    }.get(exc.status_code, "http_error")
    return _err(exc.status_code, type_, msg, request.url.path)


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # pydantic-ошибки (422) — компактно
    msg = "; ".join(
        f"{'.'.join(str(x) for x in e['loc'][1:]) or 'body'}: {e['msg']}"
        for e in exc.errors()
    ) or "validation error"
    return _err(422, "validation_error", msg, request.url.path)


@app.exception_handler(Exception)
async def _unexpected_handler(request: Request, exc: Exception) -> JSONResponse:
    # Не логируем в response — только в stderr для отладки.
    traceback.print_exc()
    return _err(500, "internal_error",
                f"{type(exc).__name__}: {exc}", request.url.path)


# Роутеры подключаются ДО mount статики (catch-all).
app.include_router(library_router.router)
app.include_router(rag_router.router)
app.include_router(notes_router.router)
app.include_router(graph_router.router)
app.include_router(jobs_router.router)


# Статика монтируется ПОСЛЕДНЕЙ (catch-all для "/").
_STATIC_DIR = get_settings().project_root / "web" / "static"
app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")