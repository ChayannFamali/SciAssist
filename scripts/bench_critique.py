"""Bench критики (Call 4, роль reasoning): кандидат vs прод-базлайн.

Вход = прод-вход Call 4: raw-md статьи, _truncate(5000 слов), промпт critique.md
(library_block='' — сравнимо с замером 26b 2026-10-21). Параметры = спека роли
reasoning из роутера (temp 0.4, max_tokens 3000); timeout — наблюдательный.

Метрики: wall, parse (5 ключей/salvage), n claims, quote-grounding
(доля цитат substring-in-text — анти-галлюцинационная метрика), RU, raw-хвост.
Baseline 26b: прод-заметка @card2020little.md (см. отчёт) + yaml-замеры
(232–352с, сжатие хвостов цитат).

Run: .venv/bin/python scripts/bench_critique.py [--model ID] [--citekey card2020little]
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

from sciassist.config import get_settings
from sciassist.note_generation.obsidian_builder import (
    _make_messages, _parse_json, _render_prompt, _load_prompt,
    _quote_in_text, _truncate, _NOTE_MAX_WORDS,
)
from sciassist.utils.lm_studio_client import LMStudioClient

console = Console()
KEYS = ["strengths", "weaknesses", "missing_experiments", "reproducibility", "overall"]


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="qwen/qwen3-30b-a3b")
    p.add_argument("--citekey", default="card2020little")
    p.add_argument("--timeout", type=float, default=1800.0)
    p.add_argument("--temp", type=float, default=0.4)
    p.add_argument("--max_tokens", type=int, default=3000)
    p.add_argument("--json", default="/tmp/bench_critique.json")
    args = p.parse_args()

    raw_dir = Path(get_settings().raw_markdown_path)
    md = next(iter(raw_dir.glob(f"*{args.citekey}*.md")), None)
    if md is None:
        md = next(iter(raw_dir.glob("*.md")), None)
        console.print(f"[yellow]⚠ {args.citekey}.md не найден, беру {md.name}[/]")
    full_text = md.read_text(encoding="utf-8")
    paper = _truncate(full_text, _NOTE_MAX_WORDS)
    console.print(f"вход: {md.name} | {len(full_text.split())} слов → усечение {len(paper.split())}")

    prompt = _render_prompt(
        _load_prompt("critique"),
        paper_text=paper,
        library_block="",            # сравнимо с 26b-замером 2026-10-21
    )

    llm = LMStudioClient()
    t0 = time.perf_counter()
    raw = await llm.chat(
        messages=_make_messages(prompt),
        model=args.model, temperature=args.temp,
        timeout=args.timeout, max_tokens=args.max_tokens,
    )
    wall = round(time.perf_counter() - t0, 1)
    console.print(f"[bold]wall = {wall}с[/]")

    crit = _parse_json(raw)
    keys_ok = [k for k in KEYS if k in crit]
    salvage = "⚠ salvage" if ("```" in raw or not raw.strip().startswith("{")) and crit else ("json" if crit else "FAIL")
    claims = []
    for cl in (crit.get("strengths", []) or []) + (crit.get("weaknesses", []) or []):
        if isinstance(cl, dict) and cl.get("quote"):
            claims.append(cl)
    grounded = sum(1 for c in claims if _quote_in_text(str(c["quote"]), full_text))
    ru = bool(re.search(r"[А-Яа-яЁё]", raw))
    n_st = len(crit.get("strengths", []) or [])
    n_wk = len(crit.get("weaknesses", []) or [])

    # thinking-форензика: пустой/обрезанный контент, теги в raw
    thinking_tags = bool(re.search(r"<\|?(?:begin_)?think(?:ing)?", raw))

    out = {
        "model": args.model, "citekey": args.citekey, "input": md.name,
        "wall_s": wall, "parse": salvage, "keys_present": keys_ok,
        "n_strengths": n_st, "n_weaknesses": n_wk,
        "quotes_total": len(claims), "quotes_grounded": grounded,
        "ru": ru, "thinking_tags_in_raw": thinking_tags,
        "raw_head": raw[:400], "raw_len": len(raw),
    }
    console.print(f"parse={salvage} keys={len(keys_ok)}/5 claims={len(claims)} "
                  f"grounded={grounded}/{len(claims)} ru={ru} think_tags={thinking_tags} raw_len={len(raw)}")
    Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    console.print(f"[bold]чекпоинт: {args.json}[/]")
    # полный raw — для форензики
    Path(args.json.replace(".json", ".raw.txt")).write_text(raw, encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
