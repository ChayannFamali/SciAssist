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
            candidate = m.group(1)
            try:
                return json.loads(candidate)
            except Exception:
                pass
            # e4b ломает JSON-сериализацию в RU-тексте (geiping-прогоны 2026-10-22: salvage 1/3
            # ключей, две морфологии: \"-остатки и голые пары кавычек, плюс LaTeX \gamma).
            # Конвейер нормализации: \" → кавычка; невалидные \-эскейпы (LaTeX) → без слеша;
            # пары кавычек в середине значений → «ёлочки» (структурные ":" "," не трогаем).
            fixed = candidate.replace('\\"', '"')
            fixed = re.sub(r'\\(?![nrtbu"\\/\\])', '', fixed)
            fixed = re.sub(
                r'(?<=[^\s:,{}\[\]])\s*"([^"\n:,]{1,80})"\s*(?=[^\s:,}\]])',
                r" «\1» ", fixed,
            )
            if fixed != candidate:
                try:
                    return json.loads(fixed)
                except Exception:
                    pass
    salvaged = _salvage_truncated_json(text)
    if salvaged:
        logger.info(f"JSON восстановлен salvage-ом ({len(salvaged)} ключей) — ответ битый (экранирование/обрезка)")
        return salvaged

    logger.warning(f"JSON не найден в ответе. Первые 300 симв.: {text[:300]!r}")
    return {}


def _filter_grounded(entities: dict, full_text: str, citekey: str) -> dict:
    """Отсев few-shot-галлюцинаций: терм остаётся, только если его слова
    встречаются в тексте статьи (класс отравлений bert/imagenet/mrcp —
    замерено: 3n стабильно тянет ImageNet из примера промпта)."""
    if not entities:
        return entities
    paper_words = {w.lower() for w in re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", full_text)}
    filtered = {}
    dropped = {}
    for cat in ("methods", "datasets", "concepts"):
        kept = []
        for term in entities.get(cat, []) or []:
            term = str(term)
            words = [w for w in re.findall(r"[a-zа-яё0-9]+", normalize_entity(term).lower()) if len(w) > 1]
            if words and any(w in paper_words for w in words):
                kept.append(term)
            else:
                dropped.setdefault(cat, []).append(term)
        filtered[cat] = kept
    if dropped:
        logger.warning(f"{citekey}: entity-фильтр отсеял (нет в тексте): {dropped}")
    return filtered


_NUM_RE = re.compile(r"\d[\d.,]*\d|\d")


def _numbers_in(text: str) -> set[str]:
    """Числовые токены с нормализацией тысячных разделителей
    («2,000»/«2 000»/NBSP → «2000») — иначе перевод/экстракция чисел
    одинаковой величины не сверяются."""
    text = re.sub(r"(?<=\d)[ ,\u00A0](?=\d{3}(?:\D|$))", "", text)
    out: set[str] = set()
    for m in _NUM_RE.finditer(text):
        s = m.group(0).rstrip(".").rstrip(",")
        if s:
            out.add(s)
    return out


def _assert_numbers_grounded(section: str, full_text: str, citekey: str, where: str) -> str:
    """Числа вывода обязаны встречаться в статье (класс «~75% → 75.6%»).
    Маркер вместо отсева: ложный маркер виден и проверяем, тихое искажение — нет."""
    paper = _numbers_in(full_text)
    bad = sorted(n for n in _numbers_in(section) if n not in paper)
    if bad:
        logger.warning(f"{citekey}: [{where}] числа не найдены в статье: {bad}")
        return (
            f"{section}\n\n"
            f"> [!warning] ⚠ Числа {', '.join(bad)} не найдены в оригинале статьи — проверь вручную."
        )
    return section


def _quote_in_text(quote: str, full_text: str) -> bool:
    norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()
    return bool(quote) and norm(quote) in norm(full_text)


def _ground_claims(claims: list, full_text: str, citekey: str, where: str) -> list[str]:
    """Критика обязана опираться на текст: претензия + дословная цитата,
    цитата проверяется substring-ом по статье. Неподтверждённое — маркер,
    не отсев (модель может сжать цитату — это подозрение, не приговор)."""
    out: list[str] = []
    for c in claims if isinstance(claims, list) else []:
        if isinstance(c, dict):
            claim = str(c.get("claim", "")).strip()
            quote = str(c.get("quote", "")).strip()
        else:
            claim, quote = str(c).strip(), ""
        if not claim:
            continue
        if quote and _quote_in_text(quote, full_text):
            out.append(f"{claim}\n  > 📖 «{quote}»")
        elif quote:
            logger.warning(f"{citekey}: [{where}] цитата не найдена в статье: {quote[:80]!r}")
            out.append(f"{claim} *(цитата не подтверждена текстом)*\n  > 📖 «{quote}»")
        else:
            logger.warning(f"{citekey}: [{where}] претензия без цитаты: {claim[:80]!r}")
            out.append(f"{claim} *(без цитаты из статьи)*")
    return out



    return out


def _latin_terms_report(section: str, full_text: str, citekey: str, where: str) -> None:
    """Латинские слова RU-переведённой секции ≈ термины (перевод оставляет термины EN);
    термин не из статьи = кандидат на выдумку. Отчёт в лог, не отсев:
    генерация может склонять/вставлять служебные слова."""
    if not section:
        return
    paper = {w.lower() for w in re.findall(r"[a-z][a-z\-0-9]{2,}", full_text.lower())}
    words = {w for w in re.findall(r"[A-Za-z][A-Za-z\-0-9]{2,}", section)
             if w.lower() not in paper}
    if words:
        logger.warning(f"{citekey}: [{where}] термы не найдены в статье "
                       f"(кандидаты на выдумку): {sorted(words)[:12]}")


def _wikilink_body(content: str, slugs: list[str]) -> str:
    """Автолинки терминов в ТЕЛЕ заметки (не в frontmatter/секции Связи):
    первое вхождение терма → [[slug|как в тексте]]. Даёт рёбра графа из контекста,
    а не только из перечня внизу. Слаг-паттерн допускает пробел/дефис между словами
    («statistical power» → statistical-power); границы не трогают составные
    («sst» не линкуется внутри «SST-2») и существующие [[ссылки]]."""
    head, sep, body = content.partition("\n\n# ")
    if not sep:
        head, body = "", content
    for slug in slugs:
        pat = (r"(?<![\w\]\[])"
               + r"[\s\-]".join(re.escape(p) for p in slug.split("-"))
               + r"(?![\w\-])")
        m = re.search(pat, body, flags=re.I)
        if m:
            body = body[:m.start()] + f"[[{slug}|{m.group(0)}]]" + body[m.end():]
    return head + sep + body


async def _related_papers(llm, structure: dict, citekey: str, exclude: str) -> list[str]:
    """Рёбра статья-статья: embedding method+results → ближайшие в papers_full
    (кроме самой статьи) → related-тег в frontmatter заметки."""
    try:
        from sciassist.indexing.rag_indexer import RAGIndexer
        q_text = " ".join(filter(None, [
            str(structure.get("method", "")), str(structure.get("results", "")),
        ]))[:4000]
        if not q_text.strip():
            return []
        emb = (await llm.embed([q_text]))[0]
        col = RAGIndexer()._col("papers_full")
        res = col.query(query_embeddings=[emb], n_results=12, include=["metadatas"])
        keys = {m.get("citekey") for m in res["metadatas"][0] if m.get("citekey")}
        keys.discard(exclude)
        return sorted(keys)[:3]
    except Exception as e:
        logger.warning(f"{citekey}: related-тег не построен: {e}")
        return []

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
    for _k in ("tldr", "problem", "limitations"):
        if summary.get(_k):
            summary[_k] = _assert_numbers_grounded(str(summary[_k]), full_text, citekey, f"summary/{_k}")

    # ── LLM Call 2: Structure ────────────────────────────────────────
    logger.info(f"{citekey}: [2/4] extract_structure…")
    # extraction-задача; deep_analysis остаётся для синтеза (gaps/related-work)
    spec = router.select("structure")
    raw = await llm.chat(
        messages=_make_messages(_render_prompt(
            _load_prompt("extract_structure"),
            paper_text=_truncate(full_text, _NOTE_MAX_WORDS),
        )),
        model=spec.name, temperature=spec.temperature, timeout=spec.timeout,max_tokens=spec.max_tokens,
    )
    structure = _parse_json(raw)

    # ── LLM Call 2b: Structure → RU (gemma-3n отдаёт EN-JSON: зеркалит system+статью) ──
    if structure:
        logger.info(f"{citekey}: [2b] translate_structure…")
        tr_spec = router.select("translation")
        tr_raw = await llm.chat(
            messages=[
                {"role": "system", "content": _load_prompt("translate_structure").split("{structure_json}")[0].strip()},
                {"role": "user", "content": _load_prompt("translate_structure").split("{structure_json}")[1].strip() + "\n\n" + json.dumps(structure, ensure_ascii=False)},
            ],
            model=tr_spec.name, temperature=tr_spec.temperature, timeout=tr_spec.timeout,
            max_tokens=tr_spec.max_tokens,
        )
        tr_struct = _parse_json(tr_raw)
        if tr_struct:
            # числа обязаны пережить перевод нетронутыми; расхождение = искажение факта
            src_nums = _numbers_in(json.dumps(structure, ensure_ascii=False))
            tr_nums = _numbers_in(json.dumps(tr_struct, ensure_ascii=False))
            if src_nums - tr_nums or tr_nums - src_nums:
                logger.warning(
                    f"{citekey}: перевод искажает числа "
                    f"(потеряно {sorted(src_nums - tr_nums)}, появилось {sorted(tr_nums - src_nums)}) — откат на EN-JSON"
                )
            else:
                structure = tr_struct
        else:
            logger.warning(f"{citekey}: перевод structure не распарсен — остаётся EN-JSON")

    # ось 2: термины structure не из статьи = кандидаты на выдумку (отчёт, не отсев)
    for _k in ("method", "experiments", "results"):
        _latin_terms_report(str(structure.get(_k, "")), full_text, citekey, f"structure/{_k}")
    # ось 3: наблюдаемость потерь чисел в extraction
    if structure:
        paper_nums = _numbers_in(_truncate(full_text, _NOTE_MAX_WORDS))
        got = _numbers_in(json.dumps(structure, ensure_ascii=False))
        logger.info(f"{citekey}: числа: extraction захватил {len(got)} из {len(paper_nums)} "
                    f"числовых токенов входа")

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
    entities = _filter_grounded(entities, full_text, citekey)

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
    # претензии о статье обязаны опираться на текст: quote-поля проверяются substring-ом
    critique["strengths"] = _ground_claims(critique.get("strengths", []), full_text, citekey, "critique/strengths")
    critique["weaknesses"] = _ground_claims(critique.get("weaknesses", []), full_text, citekey, "critique/weaknesses")

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

    # ── Related (рёбра статья↔статья) ─────────────────────────────────
    related = await _related_papers(llm, structure, citekey, exclude=citekey)

    # ── Render ───────────────────────────────────────────────────────
    key_ideas = structure.get("key_ideas", [])
    if isinstance(key_ideas, str):
        key_ideas = [key_ideas]

    # пустая секция = тихая деградация (v2-прогон: JSON-фейл structure остался незаметным)
    missing = "> [!warning] ⚠ Секция не извлечена — перезапусти process."

    content = _load_template().render(
        citekey=citekey,
        item_key=item.key,
        title=item.title,
        authors=[str(a) for a in item.authors],
        year=item.year or "",
        doi=item.doi or "",
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        auto_tags=auto_tags,
        tldr=summary.get("tldr") or missing,
        problem=summary.get("problem") or missing,
        limitations=summary.get("limitations") or missing,
        method=structure.get("method") or missing,
        key_ideas=key_ideas,
        experiments=structure.get("experiments") or missing,
        results=structure.get("results") or missing,
        figures=figures,
        strengths=critique.get("strengths", []),
        weaknesses=critique.get("weaknesses", []),
        overall=critique.get("overall", ""),
        missing_experiments=critique.get("missing_experiments", "—"),
        reproducibility=critique.get("reproducibility", "—"),
        related=related,
        methods=methods,
        datasets=datasets,
        concepts=concepts,
        my_thoughts=manual.get("my_thoughts", ""),
        reading_notes=manual.get("reading_notes", ""),
    )

    # ось 1: термы в теле заметки → wiki-ссылки (рёбра графа из контекста)
    content = _wikilink_body(content, datasets + methods + concepts)
    note_path.write_text(content, encoding="utf-8")
    logger.info(f"{citekey}: заметка → {note_path}")
    return note_path
