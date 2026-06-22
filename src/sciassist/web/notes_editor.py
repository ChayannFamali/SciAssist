"""Безопасное точечное редактирование заметок.

Только три операции (из TASKS.md T3.1):
  • link   — добавить [[target]] в секцию «🔗 Связи»
  • tag    — добавить тег во frontmatter
  • thought — добавить строку в секцию «💡 Мои мысли»

Защита:
  • mtime-check: если файл менялся с момента чтения клиентом → 409
  • атомарная запись: tmp-файл → os.replace
  • ручные секции (🔍 Критический разбор, 📝 Заметки при чтении) НЕ трогаем
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import frontmatter

from sciassist.config import get_settings
from sciassist.web.notes_reader import _note_path

# Точные имена секций из note_template.md (используются как есть)
SECTION_LINKS = "## 🔗 Связи"
SECTION_THOUGHT = "## 💡 Мои мысли"

_LINK_RE = re.compile(r"\[\[([^\]\n]+?)\]\]")


class NoteConflictError(Exception):
    """Файл заметки изменился с момента чтения клиентом (mtime mismatch)."""
    def __init__(self, expected_mtime: int, current_mtime: int) -> None:
        super().__init__(
            f"заметка изменилась: ожидался mtime={expected_mtime}, текущий={current_mtime}"
        )
        self.expected_mtime = expected_mtime
        self.current_mtime = current_mtime


class NoteNotFoundError(Exception):
    pass


def _read_checked(path: Path, expected_mtime: int | None) -> tuple[str, frontmatter.Post, int]:
    """Прочитать файл с проверкой mtime (если указан)."""
    if not path.exists():
        raise NoteNotFoundError(f"заметка не найдена: {path}")

    stat = path.stat()
    if expected_mtime is not None and int(stat.st_mtime) != int(expected_mtime):
        raise NoteConflictError(int(expected_mtime), int(stat.st_mtime))

    raw = path.read_text(encoding="utf-8")
    post = frontmatter.loads(raw)
    return raw, post, int(stat.st_mtime)


def _atomic_write(path: Path, content: str) -> None:
    """Записать content в path атомарно: tmp + os.replace."""
    # tmp рядом с файлом (чтобы был на той же FS — иначе os.replace может быть cross-device)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _section_body(body: str, header: str) -> tuple[str, str, str]:
    """Найти секцию `header` (например `## 🔗 Связи`) в body.

    Возвращает (prefix, content, suffix):
      • prefix — текст ДО секции (включая предыдущий \n)
      • content — текст ВНУТРИ секции (без самого `## header`)
      • suffix — текст ПОСЛЕ секции
    """
    lines = body.split("\n")
    out_prefix: list[str] = []
    out_content: list[str] = []
    out_suffix: list[str] = []
    i = 0
    # skip prefix
    while i < len(lines):
        line = lines[i]
        if line.strip() == header:
            break
        out_prefix.append(line)
        i += 1
    if i >= len(lines):
        # секции нет — добавляем её в конец
        return ("\n".join(out_prefix), "", "")

    # consume content until next `## ` or end
    i += 1  # past the header
    while i < len(lines):
        line = lines[i]
        if line.startswith("## ") and line.strip() == header:
            # duplicate header? skip
            i += 1
            continue
        if line.startswith("## "):
            # next section
            break
        out_content.append(line)
        i += 1
    out_suffix = lines[i:]
    return ("\n".join(out_prefix), "\n".join(out_content), "\n".join(out_suffix))


def _replace_section(body: str, header: str, new_content: str) -> str:
    """Заменить содержимое секции `header` на `new_content` (без самого header)."""
    prefix, _old, suffix = _section_body(body, header)
    # new_content может заканчиваться или не заканчиваться на \n
    nc = new_content.rstrip("\n")
    # собрать
    pieces = [prefix.rstrip("\n"), "", header, nc, ""]
    # suffix уже содержит следующие секции (начиная с `## ...`)
    if suffix.strip():
        pieces.append(suffix.lstrip("\n"))
    return "\n".join(pieces).rstrip() + "\n"


def _append_to_section(body: str, header: str, line: str) -> str:
    """Добавить `line` в конец секции `header`. Если секции нет — создать."""
    prefix, old, suffix = _section_body(body, header)
    if not old.strip():
        # секции нет — создаём
        if suffix.strip():
            return f"{prefix.rstrip()}\n\n{header}\n{line}\n\n{suffix.lstrip()}"
        else:
            return f"{prefix.rstrip()}\n\n{header}\n{line}\n"
    # есть — добавить
    new_content = (old.rstrip() + "\n" + line).rstrip()
    return _replace_section(body, header, new_content)


def add_link(citekey: str, target: str, *, expected_mtime: int | None = None) -> dict:
    """Добавить [[target]] в секцию «🔗 Связи» заметки citekey.

    Идемпотентно: если [[target]] уже есть — ничего не делает.
    """
    if not target.strip():
        raise ValueError("target пустой")
    target = target.strip().lstrip("@")

    cfg = get_settings()
    path = _note_path(cfg.obsidian_vault, cfg.obsidian_papers_folder, citekey)
    raw, post, mtime = _read_checked(path, expected_mtime)

    body = post.content
    # Идемпотентность: если [[target]] уже в любом месте body — выходим
    if re.search(rf"\[\[{re.escape(target)}\]\]", body):
        return {"ok": True, "changed": False, "mtime": mtime, "path": str(path)}

    new_line = f"- [[{target}]]"
    new_body = _append_to_section(body, SECTION_LINKS, new_line)

    # Сохраняем frontmatter как есть
    new_post = frontmatter.Post(new_body, **dict(post.metadata))
    new_raw = frontmatter.dumps(new_post) + "\n"

    _atomic_write(path, new_raw)
    new_mtime = int(path.stat().st_mtime)
    return {"ok": True, "changed": True, "mtime": new_mtime, "path": str(path)}


def add_tag(citekey: str, tag: str, *, expected_mtime: int | None = None) -> dict:
    """Добавить тег во frontmatter (идемпотентно)."""
    tag = tag.strip()
    if not tag:
        raise ValueError("tag пустой")
    # Obsidian-теги без пробелов
    tag = re.sub(r"\s+", "-", tag)

    cfg = get_settings()
    path = _note_path(cfg.obsidian_vault, cfg.obsidian_papers_folder, citekey)
    raw, post, mtime = _read_checked(path, expected_mtime)

    tags = list(post.metadata.get("tags", []) or [])
    if tag in tags:
        return {"ok": True, "changed": False, "mtime": mtime, "path": str(path)}
    tags.append(tag)

    new_meta = dict(post.metadata)
    new_meta["tags"] = tags
    new_post = frontmatter.Post(post.content, **new_meta)
    new_raw = frontmatter.dumps(new_post) + "\n"

    _atomic_write(path, new_raw)
    new_mtime = int(path.stat().st_mtime)
    return {"ok": True, "changed": True, "mtime": new_mtime, "path": str(path)}


def add_thought(citekey: str, text: str, *, expected_mtime: int | None = None) -> dict:
    """Добавить строку в секцию «💡 Мои мысли» (с timestamp).

    text может быть многострочным — каждая строка станет элементом списка.
    """
    text = text.strip()
    if not text:
        raise ValueError("text пустой")

    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    cfg = get_settings()
    path = _note_path(cfg.obsidian_vault, cfg.obsidian_papers_folder, citekey)
    raw, post, mtime = _read_checked(path, expected_mtime)

    lines = [f"- {ts}: {ln.strip()}" for ln in text.splitlines() if ln.strip()]
    body = post.content
    for line in lines:
        body = _append_to_section(body, SECTION_THOUGHT, line)

    new_post = frontmatter.Post(body, **dict(post.metadata))
    new_raw = frontmatter.dumps(new_post) + "\n"

    _atomic_write(path, new_raw)
    new_mtime = int(path.stat().st_mtime)
    return {"ok": True, "changed": True, "mtime": new_mtime, "path": str(path)}


__all__ = [
    "add_link", "add_tag", "add_thought",
    "NoteConflictError", "NoteNotFoundError",
]