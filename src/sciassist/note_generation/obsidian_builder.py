"""Generate Obsidian notes from processed paper data."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Template
from loguru import logger

from sciassist.config import get_settings, get_yaml_config
from sciassist.router.model_router import ModelRouter
from sciassist.utils.lm_studio_client import LMStudioClient
from sciassist.utils.zotero_client import ZoteroClient, ZoteroItem

_NOTE_MAX_WORDS = 5000
# ---------------------------------------------------------------------------
# LLM messaging helpers
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are a scientific research assistant. "
    "Output ONLY the requested JSON object. "
    "No explanations, no markdown fences, no thinking. Just JSON."
)

def _make_messages(prompt_text: str) -> list[dict[str, str]]:
    """Wrap prompt with system message + /no_think prefix for Qwen3."""
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"/no_think\n{prompt_text}"},
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _salvage_truncated_json(text: str) -> dict:
    """Best-effort ремонт обрезанного JSON (LLM упёрся в max_tokens)."""
    start = text.find("{")
    if start == -1:
        return {}
    s = text[start:]
    for cut in range(len(s), 0, -1):
        frag = s[:cut].rstrip().rstrip(",")
        if frag.count('"') % 2 != 0:      # обрыв внутри строки — пропускаем
            continue
        opens_sq = frag.count("[") - frag.count("]")
        opens_cu = frag.count("{") - frag.count("}")
        candidate = frag + "]" * max(0, opens_sq) + "}" * max(0, opens_cu)
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return {}


def _parse_json(text: str) -> dict:
    """Robustly extract JSON from LLM response."""
    if not text.strip():
        logger.warning("LLM вернул пустой ответ (thinking mode? контекст?)")
        return {}

    for pattern in (r"```(?:json)?\s*(\{.*?\})\s*```", r"(\{.*\})"):
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
    salvaged = _salvage_truncated_json(text)
    if salvaged:
        logger.info(f"JSON восстановлен из обрезанного ответа ({len(salvaged)} ключей)")
        return salvaged

    logger.warning(f"JSON не найден в ответе. Первые 300 симв.: {text[:300]!r}")
    return {}



def _truncate(text: str, max_words: int, reserve: int = 0) -> str:
    """Truncate to max_words. reserve ignored (kept for compat)."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "\n\n[...обрезано...]"


def normalize_entity(name: str) -> str:
    """Convert entity name to kebab-case without versions."""
    name = re.sub(r"[-\s]*(v\d+(\.\d+)*|[-\s]\d+(\.\d+)*[bBmMkK]?)\s*$", "", name, flags=re.I)
    name = name.lower().strip()
    name = re.sub(r"[\s_/]+", "-", name)
    name = re.sub(r"[^a-z0-9-]", "", name)
    name = re.sub(r"-+", "-", name).strip("-")
    return name


def _load_template() -> Template:
    p = get_settings().project_root / "configs" / "prompts" / "note_template.md"
    return Template(p.read_text(encoding="utf-8"))


def _load_prompt(name: str) -> str:
    p = get_settings().project_root / "configs" / "prompts" / f"{name}.md"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def _render_prompt(template_text: str, **kwargs) -> str:
    return Template(template_text).render(**kwargs)


def _extract_manual_sections(content: str) -> dict[str, str]:
    """Preserve hand-written sections when regenerating."""
    sections: dict[str, str] = {}
    for key, header in [("my_thoughts", "## 💡 Мои мысли"), ("reading_notes", "## 📝 Заметки при чтении")]:
        m = re.search(rf"{re.escape(header)}\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
        if m:
            val = m.group(1).strip()
            if val and val != "<!-- заполняется вручную -->":
                sections[key] = val
    return sections


def _ensure_stub(folder: Path, name: str) -> None:
    """Create a stub note if it doesn't exist."""
    path = folder / f"{name}.md"
    if not path.exists():
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\ntags: [stub]\n---\n\n# {name}\n\n"
            "> [!info] Stub\n"
            "> Заметка создана автоматически. Заполни вручную.\n",
            encoding="utf-8",
        )
        logger.debug(f"Stub создан: {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def build_note(citekey: str, item: ZoteroItem, force: bool = False) -> Path:
    """
    Generate Obsidian note for a paper.
    Saves to D:/SciVault/papers/@{citekey}.md
    Returns note path.
    """
    cfg = get_settings()
    vault = cfg.obsidian_vault
    note_path = vault / cfg.obsidian_papers_folder / f"@{citekey}.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)

    # Protect manual notes
    manual: dict[str, str] = {}
    if note_path.exists():
        existing = note_path.read_text(encoding="utf-8")
        if "#manual_notes" in existing and not force:
            logger.info(f"{citekey}: защищена тегом #manual_notes, пропуск")
            return note_path
        manual = _extract_manual_sections(existing)

    # Load source data
    md_path = cfg.raw_markdown_path / f"{citekey}.md"
    if not md_path.exists():
        raise FileNotFoundError(f"Markdown не найден: {md_path}. Сначала запусти process.")

    full_text = md_path.read_text(encoding="utf-8")

    figs_path = cfg.extracted_figures_path / citekey / "figures.json"
    figures = json.loads(figs_path.read_text(encoding="utf-8")) if figs_path.exists() else []

    router = ModelRouter()
    llm = LMStudioClient()

    # ── LLM Call 1: Summary ──────────────────────────────────────────
    logger.info(f"{citekey}: [1/4] summary…")
    spec = router.select("summary")
    raw = await llm.chat(
        messages=_make_messages(_render_prompt(
            _load_prompt("summary"),
            paper_text=_truncate(full_text, _NOTE_MAX_WORDS),
        )),
        model=spec.name, temperature=spec.temperature, timeout=spec.timeout,max_tokens=spec.max_tokens,
    )
    summary = _parse_json(raw)

    # ── LLM Call 2: Structure ────────────────────────────────────────
    logger.info(f"{citekey}: [2/4] extract_structure…")
    spec = router.select("deep_analysis")
    raw = await llm.chat(
        messages=_make_messages(_render_prompt(
            _load_prompt("extract_structure"),
            paper_text=_truncate(full_text, _NOTE_MAX_WORDS),
        )),
        model=spec.name, temperature=spec.temperature, timeout=spec.timeout,max_tokens=spec.max_tokens,
    )
    structure = _parse_json(raw)

    # ── LLM Call 3: Entities ─────────────────────────────────────────
    logger.info(f"{citekey}: [3/4] entity_extraction…")
    spec = router.select("entity_extraction")
    raw = await llm.chat(
        messages=_make_messages(_render_prompt(
            _load_prompt("entity_extraction"),
            paper_text=_truncate(full_text, _NOTE_MAX_WORDS),
        )),
        model=spec.name, temperature=spec.temperature, timeout=spec.timeout,max_tokens=spec.max_tokens,
    )
    entities = _parse_json(raw)

    # ── LLM Call 4: Critique ─────────────────────────────────────────
    logger.info(f"{citekey}: [4/4] critique…")
    spec = router.select("reasoning")
    raw = await llm.chat(
        messages=_make_messages(_render_prompt(
            _load_prompt("critique"),
            paper_text=_truncate(full_text, _NOTE_MAX_WORDS),
        )),
        model=spec.name, temperature=spec.temperature, timeout=spec.timeout,max_tokens=spec.max_tokens,
    )
    critique = _parse_json(raw)

    # ── Normalize entities ───────────────────────────────────────────
    methods   = list({normalize_entity(e) for e in entities.get("methods", []) if e})
    datasets  = list({normalize_entity(e) for e in entities.get("datasets", []) if e})
    concepts  = list({normalize_entity(e) for e in entities.get("concepts", []) if e})

    # ── Create stubs ─────────────────────────────────────────────────
    for name in methods + concepts:
        _ensure_stub(vault / cfg.obsidian_concepts_folder, name)
    for name in datasets:
        _ensure_stub(vault / cfg.obsidian_datasets_folder, name)

    # ── Auto tags ────────────────────────────────────────────────────
    auto_tags = methods[:5]

    # ── Render ───────────────────────────────────────────────────────
    key_ideas = structure.get("key_ideas", [])
    if isinstance(key_ideas, str):
        key_ideas = [key_ideas]

    content = _load_template().render(
        citekey=citekey,
        item_key=item.key,
        title=item.title,
        authors=[str(a) for a in item.authors],
        year=item.year or "",
        doi=item.doi or "",
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        auto_tags=auto_tags,
        tldr=summary.get("tldr", ""),
        problem=summary.get("problem", ""),
        limitations=summary.get("limitations", ""),
        method=structure.get("method", ""),
        key_ideas=key_ideas,
        experiments=structure.get("experiments", ""),
        results=structure.get("results", ""),
        figures=figures,
        strengths=critique.get("strengths", []),
        weaknesses=critique.get("weaknesses", []),
        overall=critique.get("overall", ""),
        methods=methods,
        datasets=datasets,
        concepts=concepts,
        my_thoughts=manual.get("my_thoughts", ""),
        reading_notes=manual.get("reading_notes", ""),
    )

    note_path.write_text(content, encoding="utf-8")
    logger.info(f"{citekey}: заметка → {note_path}")
    return note_path
