"""Notes API — одиночное и пакетное чтение заметок."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Path, Query
from pydantic import BaseModel

from sciassist.web.deps import run_sync
from sciassist.web.notes_editor import (
    NoteConflictError,
    NoteNotFoundError,
    add_link,
    add_tag,
    add_thought,
)
from sciassist.web.notes_reader import read_note

router = APIRouter()

_MAX_BATCH = 5


# ─────────────────────────────────────────────────────────────────────────────
# Read
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/api/note/{citekey}")
async def note_one(
    citekey: Annotated[str, Path(min_length=1, description="Citekey (с/без @)")],
) -> dict:
    """Одна заметка: {citekey, markdown, sections, links, tags, year, mtime}."""
    def _read() -> dict:
        return read_note(citekey)

    try:
        return await run_sync(_read)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка чтения: {e}")


@router.get("/api/notes")
async def notes_batch(
    keys: Annotated[str, Query(description="Citekey через запятую (≤ 5)")],
) -> dict:
    """Пакетное чтение заметок (до 5). Возвращает {notes, truncated, missing}."""
    requested = [k.strip() for k in keys.split(",") if k.strip()]
    truncated = False
    if len(requested) > _MAX_BATCH:
        requested = requested[:_MAX_BATCH]
        truncated = True

    def _read_all() -> tuple[list[dict], list[str]]:
        out: list[dict] = []
        missing: list[str] = []
        for ck in requested:
            try:
                out.append(read_note(ck))
            except FileNotFoundError:
                missing.append(ck.lstrip("@"))
        return out, missing

    notes, missing = await run_sync(_read_all)
    return {
        "notes": notes,
        "truncated": truncated,
        "missing": missing,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Write (Этап A — точечное редактирование)
# ─────────────────────────────────────────────────────────────────────────────
class LinkRequest(BaseModel):
    target: str


class TagRequest(BaseModel):
    tag: str


class ThoughtRequest(BaseModel):
    text: str


def _parse_if_mtime(if_match_mtime: str | None) -> int | None:
    """Парсить заголовок `If-Match-Mtime` (Unix timestamp в секундах)."""
    if not if_match_mtime:
        return None
    try:
        return int(if_match_mtime)
    except ValueError:
        return None


@router.post("/api/note/{citekey}/link")
async def post_note_link(
    citekey: Annotated[str, Path(min_length=1)],
    req: LinkRequest,
    if_match_mtime: Annotated[str | None, Header(description="Unix timestamp — для 409 при гонке")] = None,
) -> dict:
    """Добавить [[target]] в секцию «🔗 Связи» заметки.

    Идемпотентно: если [[target]] уже есть — возвращает changed=False.
    Передайте `If-Match-Mtime: <unix-ts>` (из GET /api/note/{ck} → mtime)
    для защиты от гонки с Obsidian.
    """
    def _do() -> dict:
        return add_link(citekey, req.target, expected_mtime=_parse_if_mtime(if_match_mtime))

    try:
        return await run_sync(_do)
    except NoteNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except NoteConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка записи: {e}")


@router.post("/api/note/{citekey}/tag")
async def post_note_tag(
    citekey: Annotated[str, Path(min_length=1)],
    req: TagRequest,
    if_match_mtime: Annotated[str | None, Header()] = None,
) -> dict:
    """Добавить тег во frontmatter заметки (идемпотентно)."""
    def _do() -> dict:
        return add_tag(citekey, req.tag, expected_mtime=_parse_if_mtime(if_match_mtime))

    try:
        return await run_sync(_do)
    except NoteNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except NoteConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка записи: {e}")


@router.post("/api/note/{citekey}/thought")
async def post_note_thought(
    citekey: Annotated[str, Path(min_length=1)],
    req: ThoughtRequest,
    if_match_mtime: Annotated[str | None, Header()] = None,
) -> dict:
    """Добавить строку в секцию «💡 Мои мысли» (с timestamp)."""
    def _do() -> dict:
        return add_thought(citekey, req.text, expected_mtime=_parse_if_mtime(if_match_mtime))

    try:
        return await run_sync(_do)
    except NoteNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except NoteConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка записи: {e}")