"""Чтение заметок Obsidian (papers) с нарезкой по секциям.

Заметки имеют фиксированный каркас (см. configs/prompts/note_template.md):
  frontmatter (YAML) → # Title → callout TL;DR →
  ## 🎯 Проблема и мотивация → ## 🔬 Метод → ## 📊 Эксперименты и Результаты →
  ## ⚠️ Ограничения → ## 🔍 Критический разбор → ## 🔗 Связи →
  ## 💡 Мои мысли → ## 📝 Заметки при чтении

Имя файла: `vault/papers/@{citekey}.md` (префикс @).
"""
from __future__ import annotations

import re
from pathlib import Path

import frontmatter

from sciassist.config import get_settings

_LINK_RE = re.compile(r"\[\[([^\]\n]+?)\]\]")
_H2_SPLIT_RE = re.compile(r"\n(?=##\s)")


def _note_path(vault: Path, papers_folder: str, citekey: str) -> Path:
    """Найти путь к заметке с учётом возможного префикса @."""
    name = citekey.lstrip("@")
    candidates = [
        vault / papers_folder / f"@{name}.md",
        vault / papers_folder / f"{name}.md",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]  # первый — канонический


def read_note(citekey: str) -> dict:
    """Прочитать заметку и вернуть {citekey, markdown, sections, links, mtime, tags, year}.

    Raises FileNotFoundError если заметки нет.
    """
    cfg = get_settings()
    path = _note_path(cfg.obsidian_vault, cfg.obsidian_papers_folder, citekey)

    if not path.exists():
        raise FileNotFoundError(f"Заметка не найдена: {path}")

    raw = path.read_text(encoding="utf-8")
    stat = path.stat()

    # Frontmatter через python-frontmatter
    post = frontmatter.loads(raw)
    meta = dict(post.metadata)
    body = post.content

    # Нарезка по H2-заголовкам (сохраняем сами заголовки в ключах)
    sections: dict[str, str] = {}
    parts = _H2_SPLIT_RE.split(body)
    for part in parts:
        if not part.strip():
            continue
        line, _, rest = part.partition("\n")
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            sections[m.group(1)] = rest.rstrip()
        else:
            # текст до первой секции (заголовок H1, callout) — кладём в "_header"
            if "_header" not in sections:
                sections["_header"] = part.rstrip()

    # Wiki-ссылки (только имена, без алиасов — уберём после | если есть)
    raw_links = _LINK_RE.findall(body)
    links: list[str] = []
    seen: set[str] = set()
    for link in raw_links:
        target = link.split("|", 1)[0].split("#", 1)[0].strip()
        target = target.lstrip("@")
        if target and target not in seen:
            seen.add(target)
            links.append(target)

    return {
        "citekey": citekey.lstrip("@"),
        "markdown": raw,
        "sections": sections,
        "links": links,
        "tags": meta.get("tags", []) or [],
        "year": meta.get("year"),
        "title": meta.get("title"),
        "mtime": int(stat.st_mtime),
    }


__all__ = ["read_note", "_LINK_RE", "_H2_SPLIT_RE"]