"""RAG Eval — измерительная скамья retrieval/ответов (замер → решение → применение).

Phase A (default): матрица вариантов против вопросов, метрики hit@K и латентность
  retrieval-пути (generate=False — ответ не генерится, chat не платится).
Phase B (--answers): полные ответы для одного варианта (--variant, default best) —
  маркеры в ответе + cite-корректность + латентность полного ask.

Run: python scripts/rag_eval.py [--answers] [--variant V4] [--json PATH]
Требует LM Studio (lfm2.5: expand/rerank; 3n: hyde/ответы; bge: dense).
Бенч-правило: пустой LM Studio (никаких параллельных load).
"""
import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rich.console import Console
from rich.table import Table

from sciassist.rag.query_engine import QueryEngine

console = Console()

OUT_DEFAULT = "/tmp/rag_eval.json"

# ── Вопросы: q, коллекция, маркеры (ожидаемы в top-K чанках), класс теста ──
# hit_all = все маркеры в выдаче; hit_any = хоть один. Negative — фаза ответов.
QUESTIONS = [
    dict(
        q="Какие датасеты GLUE авторы анализируют на статистическую мощность?",
        col="papers_full", cls="cross-lingual sparse",
        markers=["wnli", "mrpc", "sst-2"],
    ),
    dict(
        q="Какую минимальную статистическую мощность авторы считают приемлемой?",
        col="papers_full", cls="cross-lingual number",
        markers=["75"],
    ),
    dict(
        q="Какой статистический тест авторы советуют для сравнения двух систем?",
        col="papers_full", cls="cross-lingual term",
        markers=["mcnemar"],
    ),
    dict(
        q="В чём ошибка расчёта мощности после просмотра результатов эксперимента?",
        col="papers_full", cls="cross-lingual concept",
        markers=["post-hoc"],
    ),
    dict(
        q="What sample size do the authors recommend for the MRPC dataset?",
        col="papers_full", cls="en direct",
        markers=["2000"],
    ),
    dict(
        q="Что такое евклидово пространство?",
        col="textbooks", cls="ru textbook",
        markers=["евклид"],
    ),
    dict(
        q="Как определить длину вектора через скалярное произведение?",
        col="textbooks", cls="ru textbook",
        markers=["скалярн"],
    ),
    dict(
        q="Какой общий вердикт критики по статье Card?",
        col="papers_notes", cls="notes",
        markers=["принять"],
    ),
    dict(
        q="Какая точность GPT-4 на GLUE в этой статье Card?",
        col="papers_full", cls="negative", markers=[],
    ),
]

VARIANTS = {
    "V0": dict(expand_en=False, hyde=False, pool_k=5),   # старое поведение
    "V1": dict(expand_en=True,  hyde=False, pool_k=5),   # + EN-расширение
    "V2": dict(expand_en=False, hyde=True,  pool_k=5),   # + HyDE
    "V3": dict(expand_en=True,  hyde=False, pool_k=10),  # + пул 10
    "V4": dict(expand_en=True,  hyde=True,  pool_k=10),  # всё включено
}


def selfcheck_markers(engine: QueryEngine) -> dict[str, bool]:
    """Маркер обязан существовать в корпусе коллекции, иначе вопрос невалиден
    (меряем retrieval, а не наличие факта)."""
    cache: dict[str, str] = {}
    status = {}
    for q in QUESTIONS:
        if not q["markers"] or q["cls"] == "negative":
            continue
        col = q["col"]
        if col not in cache:
            col_obj = engine._indexer._col(col)
            data = col_obj.get(include=["documents"])
            cache[col] = " ".join(data.get("documents") or []).lower()
        corpus = cache[col]
        status[q["q"][:50]] = all(m.lower() in corpus for m in q["markers"])
    return status


async def phase_a(args) -> None:
    engine = QueryEngine()
    console.rule("[cyan]Self-check: маркеры в корпусе[/]")
    sc = selfcheck_markers(engine)
    bad = [k for k, v in sc.items() if not v]
    if bad:
        console.print(f"[red]⚠ НЕВАЛИДНЫЕ вопросы (маркера нет в корпусе): {bad}[/]")
        console.print("[red]  → вопрос исключён из hit-метрики[/]")
    results = {}

    for vname, vkw in VARIANTS.items():
        if args.variant and vname != args.variant and not args.answers:
            continue
        table = Table(title=f"Phase A: {vname} {vkw}", show_lines=False)
        for c in ("вопрос", "hit_all", "hit_any", "n_src", "latency"):
            table.add_column(c, justify="left" if c == "вопрос" else "center")
        rows = []
        for q in QUESTIONS:
            t0 = time.perf_counter()
            ans = await engine.ask(
                q["q"], collection=q["col"], top_k=5, generate=False,
                **{k: v for k, v in vkw.items()},
            )
            lat = round(time.perf_counter() - t0)
            docs = " ".join(getattr(s, "text", "") or s.preview for s in ans.sources).lower()
            valid = sc.get(q["q"][:50], True)
            m = [x for x in q["markers"]] if valid else None
            hit_any = any(x.lower() in docs for x in m) if m else False
            hit_all = all(x.lower() in docs for x in m) if m else False
            if q["cls"] == "negative":
                hit_any = hit_all = "n/a"
                n_bad = sum(1 for s in ans.sources if "gpt-4" in s.preview.lower())
                hit_any = "clean" if n_bad == 0 else f"dirty({n_bad})"
            rows.append(dict(
                q=q["q"], cls=q["cls"], hit_all=hit_all, hit_any=hit_any,
                n_src=len(ans.sources), latency_s=lat,
                srcs=[(s.citekey, s.section) for s in ans.sources],
            ))
            table.add_row(
                q["q"][:48], str(hit_all), str(hit_any), str(len(ans.sources)), f"{lat}с",
            )
            results.setdefault(vname, []).append(rows[-1])
        console.print(table)
        # агрегат
        scored = [r for r in rows if r["hit_any"] not in (True, False, "clean")]
        n_pos = sum(1 for r in rows if r["hit_any"] is True or r["hit_any"] == True)
        n_all = sum(1 for r in rows if r["hit_all"] is True)
        mean_lat = sum(r["latency_s"] for r in rows) / max(1, len(rows))
        console.print(
            f"[bold]{vname}: hit_all={n_all}/8 hit_any={n_pos}/8 "
            f"mean_lat={mean_lat:.0f}с[/] (n_src медиана "
            f"{sorted(r['n_src'] for r in rows)[len(rows)//2]})"
        )
        # чекпоинт ПОСЛЕ КАЖДОГО варианта (наблюдаемость: обрыв = данные живы)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)

    console.print(f"чекпоинт: {args.json}")


async def phase_b(args) -> None:
    engine = QueryEngine()
    vname = args.variant or "V4"
    vkw = VARIANTS[vname]
    console.rule(f"[cyan]Phase B: полные ответы, {vname} {vkw}[/]")
    table = Table(title="Ответы")
    for c in ("вопрос", "маркеры в ответе", "cite чист", "latency"):
        table.add_column(c)
    out = []
    for q in QUESTIONS:
        t0 = time.perf_counter()
        ans = await engine.ask(q["q"], collection=q["col"], top_k=5, **vkw)
        lat = round(time.perf_counter() - t0)
        a = ans.answer.lower()
        mk = all(x.lower() in a for x in q["markers"]) if q["markers"] else None
        cite_clean = "галлюцинация источника" not in a
        neg_ok = ("не могу подтвердить" in a or "не упоминается" in a or "нет" in a[:200]) if q["cls"] == "negative" else None
        table.add_row(q["q"][:40], str(mk), str(cite_clean), f"{lat}с")
        out.append(dict(q=q["q"], cls=q["cls"], markers_ok=mk, cite_clean=cite_clean,
                        negative_abstain=neg_ok, latency_s=lat,
                        answer=ans.answer[:500]))
    console.print(table)
    with open(args.json.replace(".json", "_answers.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    console.print(f"чекпоинт ответов: {args.json.replace('.json', '_answers.json')}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--answers", action="store_true", help="Phase B: полные ответы")
    p.add_argument("--variant", default=None, help="один вариант (V0..V4)")
    p.add_argument("--json", default=OUT_DEFAULT)
    args = p.parse_args()
    if args.answers:
        asyncio.run(phase_b(args))
    else:
        asyncio.run(phase_a(args))


if __name__ == "__main__":
    main()
