"""TX.2: эквивалентность /api/ask ↔ CLI ask — оба используют один QueryEngine.

Идея: один и тот же вопрос → CLI и API должны дать тот же `answer`, `sources` и `model`
(или как минимум ту же структуру — текст может немного отличаться из-за temperature/sampling).
"""
import json
import urllib.request


BASE = "http://127.0.0.1:8777"
Q = "Какие основные ограничения у attention mechanism в оригинальной статье?"


# 1. CLI версия
import subprocess
print(f"Q: {Q}")
print()
print("=== CLI ask ===")
r = subprocess.run(
    [".venv/Scripts/sciassist", "ask", Q, "--top", "3"],
    capture_output=True, text=True, cwd="H:\\SciAssist", timeout=60,
)
cli_out = r.stdout
# Парсим panel и таблицу — это сложно; вместо этого сравним только наличие citekeys
import re
cli_citekeys = set(re.findall(r"│ (\w+20\d\d\w+)\s+│", cli_out))
print(f"CLI cites: {cli_citekeys}")

# 2. API версия
print()
print("=== API /api/ask ===")
body = json.dumps({"question": Q, "top_k": 3, "min_score": 0.4}).encode()
req = urllib.request.Request(f"{BASE}/api/ask", data=body,
                             headers={"Content-Type": "application/json"}, method="POST")
try:
    r = urllib.request.urlopen(req, timeout=60)
    api_out = json.loads(r.read().decode())
    api_citekeys = {s["citekey"] for s in api_out.get("sources", [])}
    print(f"API cites:  {api_citekeys}")
    print(f"API model:  {api_out.get('model')}")
    api_answer = api_out.get("answer", "")
    print(f"API answer (first 200): {api_answer[:200]}")
except Exception as e:
    print(f"API err: {e}")

print()
print("=== Эквивалентность ===")
print(f"  same citekeys: {cli_citekeys == api_citekeys}")
print(f"  CLI count: {len(cli_citekeys)}, API count: {len(api_citekeys)}")