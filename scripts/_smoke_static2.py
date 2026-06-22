"""Smoke: проверяем все static + API + отсутствие CDN + health timeout."""
import re
import time
import urllib.request


def head(url, timeout=10):
    t = time.time()
    try:
        r = urllib.request.urlopen(url, timeout=timeout)
        body = r.read()
        return r.status, len(body), (time.time() - t), body[:100]
    except Exception as e:
        return -1, 0, (time.time() - t), str(e).encode()[:100]


URLS = [
    "http://127.0.0.1:8772/",
    "http://127.0.0.1:8772/style.css",
    "http://127.0.0.1:8772/app.js",
    "http://127.0.0.1:8772/vendor/cytoscape.min.js",
    "http://127.0.0.1:8772/vendor/markdown-it.min.js",
    "http://127.0.0.1:8772/api/health",  # ≤ 4s
    "http://127.0.0.1:8772/api/stats",
    "http://127.0.0.1:8772/api/graph?mode=overlay",
    "http://127.0.0.1:8772/api/note/vaswani2023attention",
    "http://127.0.0.1:8772/api/zotero/list",
    "http://127.0.0.1:8772/api/logs?tail=3",
]

for u in URLS:
    code, size, dt, _ = head(u, timeout=10)
    print(f"{code:3} {size:7} {dt:5.2f}s  {u}")

print("\n=== CDN check (must be empty) ===")
import pathlib
for p in ["web/static/index.html", "web/static/app.js", "web/static/style.css"]:
    txt = pathlib.Path(p).read_text(encoding="utf-8")
    hits = re.findall(r"https?://(?!127\.0\.0\.1|localhost)[^\s\"')]+", txt)
    if hits:
        print(f"  {p}: FAIL {hits}")
    else:
        print(f"  {p}: OK")

print("\n=== Health timeout (must be ≤ 4s) ===")
t = time.time()
try:
    r = urllib.request.urlopen("http://127.0.0.1:8772/api/health", timeout=10)
    print(f"  HTTP {r.status} in {time.time()-t:.2f}s")
    print(f"  body: {r.read()[:200].decode()}")
except Exception as e:
    print(f"  FAILED: {e}")