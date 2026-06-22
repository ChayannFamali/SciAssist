"""Проверяем app.js на синтаксические ошибки через Node, если есть."""
import subprocess
import sys

# Проверяем синтаксис через esprima или просто через node --check
# Если node нет — пропускаем

js_path = r"web\static\app.js"

# Попробуем через node
try:
    r = subprocess.run(["node", "--check", js_path], capture_output=True, text=True, timeout=10)
    print("node --check:", r.returncode)
    if r.stdout: print("stdout:", r.stdout)
    if r.stderr: print("stderr:", r.stderr)
except FileNotFoundError:
    print("node not found, trying python parsing")
    # простой sanity: считаем скобки
    with open(js_path, encoding="utf-8") as f:
        code = f.read()
    print(f"file size: {len(code)} chars")
    print(f"open braces: {code.count('{')}, close: {code.count('}')}")
    print(f"open parens: {code.count('(')}, close: {code.count(')')}")
    print(f"open brackets: {code.count('[')}, close: {code.count(']')}")
    # Check key functions exist
    for fn in ["function renderGraph", "function loadGraph", "function drawGraph",
               "function cytoscape({", "state.cy = cytoscape"]:
        print(f"  has '{fn}': {fn in code}")