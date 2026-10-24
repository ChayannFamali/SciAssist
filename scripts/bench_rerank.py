"""Bench rerank-моделей — чистое A/B на замороженном пуле кандидатов.

Зачем: роль rerank в проде используется ДВАЖДЫ (expand_en + listwise-rerank) —
замена модели в yaml меняет И пул (EN-расширение), И сортировку → конфаунд.
Здесь пул строится ОДИН раз (V4-путь: hyde + expand + hybrid retrieve + фильтры,
прод-модели), затем один и тот же пул гоняется через все кандидаты.
Плюс baseline без реранка (RRF-порядок пula) — ранкер обязан его обыгрывать.

Метрики на вопрос×модель:
  hit@5      — маркеры в top-5 ПОЛНОГО текста чанков (не preview);
  identity   — вернул исходный порядок (распарсить не смог ИЛИ так решил);
  parse_ok   — в raw-ответе есть [..] массив (identity без parse_ok = фейл);
  n_out      — размер выдачи после реранка (может выбрасывать нерелевантные);
  lat        — латентность rerank-вызова.

Run: .venv/bin/python scripts/bench_rerank.py [--limit N] [--json PATH]
Требует загруженных ВЗАРАНЕЕ: bge-m3@fp16, gemma-3n-e4b (hyde),
lfm2.5-1.2b + кандидаты (rerank). Бенч-правило: никаких load во время прогона.
"""
import argparse
import asyncio
import json
import re
import sys
import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from rich.console import Console
from rich.table import Table

from rag_eval import QUESTIONS, selfcheck_markers   # тот же eval-сет + self-check
from sciassist.rag.query_engine import QueryEngine, _SECTION_DENYLIST
from sciassist.rag.reranker import LLMReranker

console = Console()

MODELS = ["liquid/lfm2.5-1.2b", "google/gemma-3-1b", "qwen/qwen3-1.7b"]

MIN_SCORE = 0.4          # _DEFAULT_MIN_SCORE из query_engine
MAX_PER_PAPER = 3        # _DEFAULT_MAX_PER_PAPER
POOL_K = 10              # V4
TOP_K = 5


async def build_pool(engine: QueryEngine, q: str, col: str) -> list[tuple]:
    """Прод-путь ask() ДО rerank (шаги 1–3): hyde → expand → hybrid → фильтры."""
    dense_q = await engine._hyde(q)
    sparse_q = q
    if re.search(r"[А-Яа-яЁё]", q):
        en = await engine._expand_en(q)
        if en:
            sparse_q = f"{q}\n{en}"
    candidates, _ = await engine._retrieve(dense_q, sparse_q, POOL_K, col, hybrid=True)
    filtered = []
    for doc, meta, cos, in_sparse in candidates:
        section = (meta.get("section") or "").strip().lower()
        if section in _SECTION_DENYLIST:
            continue
        if not in_sparse and cos is not None and cos < MIN_SCORE:
            continue
        filtered.append((doc, meta, float(cos) if cos is not None else 0.0))
    return filtered


def top_slice(pool: list[tuple], order: list[int]) -> list[str]:
    """Прод-шаг 5 ask(): дедуп по citekey (≤3/статья) + срез до TOP_K."""
    kept: list[str] = []
    per_paper: dict[str, int] = defaultdict(int)
    for i in order:
        if i >= len(pool):
            continue
        doc, meta, _ = pool[i]
        ck = meta.get("citekey", "unknown")
        if per_paper[ck] >= MAX_PER_PAPER:
            continue
        per_paper[ck] += 1
        kept.append(doc)
        if len(kept) >= TOP_K:
            break
    return kept


def hits(top_docs: list[str], qd: dict, valid: bool):
    """hit_all/hit_any по ПОЛНОМУ тексту top-5; negative → clean/dirty."""
    docs = " ".join(top_docs).lower()
    if qd["cls"] == "negative":
        n_bad = sum(1 for d in top_docs if "gpt-4" in d.lower())
        return "clean" if n_bad == 0 else f"dirty({n_bad})", "n/a"
    if not valid:
        return "invalid", "invalid"
    m = [x.lower() for x in qd["markers"]]
    return all(x in docs for x in m), any(x in docs for x in m)


async def rerank_model(reranker: LLMReranker, model: str, q: str, docs: list[str]):
    """rerank через прод-класс с подменённой моделью + перехват raw-ответа."""
    reranker._spec = replace(reranker._spec, name=model)
    raw_box: dict = {}
    orig = reranker._llm.chat

    async def spy(*a, **kw):
        r = await orig(*a, **kw)
        raw_box["raw"] = r
        return r

    reranker._llm.chat = spy
    t0 = time.perf_counter()
    try:
        order = await reranker.rerank(q, docs)
    finally:
        reranker._llm.chat = orig
    lat = round(time.perf_counter() - t0, 1)
    raw = (raw_box.get("raw") or "").strip()
    parse_ok = bool(re.search(r"\[[\d,\s]*\]", raw))
    return order, lat, parse_ok, raw[:400]


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--json", default="/tmp/bench_rerank.json")
    p.add_argument("--limit", type=int, default=0, help="только первые N вопросов (smoke)")
    p.add_argument("--models", nargs="*", default=MODELS)
    args = p.parse_args()

    engine = QueryEngine()
    reranker = LLMReranker()
    questions = QUESTIONS[: args.limit] if args.limit else QUESTIONS

    console.rule("[cyan]Self-check: маркеры в корпусе[/]")
    sc = selfcheck_markers(engine)
    bad = [k for k, v in sc.items() if not v]
    if bad:
        console.print(f"[red]⚠ НЕВАЛИДНЫЕ вопросы (маркера нет в корпусе): {bad}[/]")

    # ── Фаза 1: заморозка пулов (один hyde/expand/retrieve на вопрос) ──
    console.rule("[cyan]Фаза 1: пулы кандидатов (V4-путь, прод-модели)[/]")
    pools: dict[str, list[tuple]] = {}
    t0 = time.perf_counter()
    for qd in questions:
        pool = await build_pool(engine, qd["q"], qd["col"])
        pools[qd["q"]] = pool
        console.print(f"  {qd['q'][:46]:<48} пул={len(pool)}")
    console.print(f"[dim]пулы готовы за {time.perf_counter() - t0:.0f}с[/]")

    results: dict[str, list[dict]] = {}
    order_cache: dict[tuple[str, str], list[int]] = {}

    # ── Baseline: без реранка (RRF-порядок) ──
    rows = []
    for qd in questions:
        pool = pools[qd["q"]]
        valid = sc.get(qd["q"][:50], True)
        hit_all, hit_any = hits(top_slice(pool, list(range(len(pool)))), qd, valid)
        rows.append(dict(q=qd["q"][:46], cls=qd["cls"], n_pool=len(pool),
                         n_out=len(pool), hit_all=hit_all, hit_any=hit_any,
                         identity=True, parse_ok=None, lat=0.0, raw=""))
    results["baseline(no-rerank)"] = rows
    t = Table(title="Baseline: без реранка (RRF-порядок)")
    for c in ("вопрос", "hit_all", "hit_any", "пул"):
        t.add_column(c, justify="left" if c == "вопрос" else "center")
    for r in rows:
        t.add_row(r["q"], str(r["hit_all"]), str(r["hit_any"]), str(r["n_pool"]))
    console.print(t)

    # ── Фаза 2: каждый кандидат на замороженных пулах ──
    for model in args.models:
        console.rule(f"[cyan]Ранкер: {model}[/]")
        t = Table(title=f"{model}")
        for c in ("вопрос", "hit_all", "hit_any", "n_out/пул", "identity", "parse_ok", "lat"):
            t.add_column(c, justify="left" if c == "вопрос" else "center")
        rows = []
        for qd in questions:
            pool = pools[qd["q"]]
            valid = sc.get(qd["q"][:50], True)
            if len(pool) <= 1:
                hit_all, hit_any = hits(top_slice(pool, list(range(len(pool)))), qd, valid)
                rows.append(dict(q=qd["q"][:46], cls=qd["cls"], n_pool=len(pool),
                                 n_out=len(pool), hit_all=hit_all, hit_any=hit_any,
                                 identity=True, parse_ok=None, lat=0.0, raw=""))
                t.add_row(qd["q"][:46], str(hit_all), str(hit_any),
                          f"{len(pool)}/{len(pool)}", "-", "-", "-")
                continue
            order, lat, parse_ok, raw = await rerank_model(
                reranker, model, qd["q"], [p[0] for p in pool])
            identity = order == list(range(len(pool)))
            n_out = len(order)
            hit_all, hit_any = hits(top_slice(pool, order), qd, valid)
            rows.append(dict(q=qd["q"][:46], cls=qd["cls"], n_pool=len(pool),
                             n_out=n_out, hit_all=hit_all, hit_any=hit_any,
                             identity=identity, parse_ok=parse_ok, lat=lat, raw=raw))
            t.add_row(qd["q"][:46], str(hit_all), str(hit_any),
                      f"{n_out}/{len(pool)}", str(identity), str(parse_ok), f"{lat:.1f}с")
            order_cache[(model, qd["q"])] = order
        console.print(t)
        scored = [r for r in rows if r["hit_any"] not in (True, False, "clean", "invalid")]
        n_all = sum(1 for r in rows if r["hit_all"] is True)
        n_any = sum(1 for r in rows if r["hit_any"] is True)
        n_clean = sum(1 for r in rows if r["hit_any"] == "clean")
        n_id = sum(1 for r in rows if r["identity"])
        n_pf = sum(1 for r in rows if r["parse_ok"] is False)
        mean_lat = sum(r["lat"] for r in rows) / max(1, len(rows))
        console.print(
            f"[bold]{model}: hit_all={n_all}/8 hit_any={n_any}/8 (+{n_clean} clean neg) "
            f"identity={n_id} parse_fail={n_pf} mean_lat={mean_lat:.1f}с[/]"
        )
        results[model] = rows
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)

    console.print(f"[bold]чекпоинт: {args.json}[/]")


if __name__ == "__main__":
    asyncio.run(main())
