"""Smoke: проверяем все static + API + отсутствие CDN."""
import re
import urllib.request


def head(url):
    try:
        r = urllib.request.urlopen(url, timeout=10)
        body = r.read()
        return r.status, len(body), body[:200]
    except Exception as e:
        return -1, 0, str(e).encode()


URLS = [
    "http://127.0.0.1:8770/",
    "http://127.0.0.1:8770/style.css",
    "http://127.0.0.1:8770/app.js",
    "http://127.0.0.1:8770/vendor/cytoscape.min.js",
    "http://127.0.0.1:8770/vendor/markdown-it.min.js",
    "http://127.0.0.1:8770/api/health",
    "http://127.0.0.1:8770/api/stats",
    "http://127.0.0.1:8770/api/graph?mode=overlay",
    "http://127.0.0.1:8770/api/note/vaswani2023attention",
]

for u in URLS:
    code, size, head_bytes = head(u)
    body_str = head_bytes.decode("utf-8", errors="replace")
    short = body_str[:80].replace("\n", " ")
    print(f"{code:3} {size:7} {u}")
    if code == 200 and not u.endswith(".js"):
        print(f"      {short!r}")

print("\n=== CDN check (must be empty) ===")
import pathlib
for p in ["web/static/index.html", "web/static/app.js", "web/static/style.css"]:
    txt = pathlib.Path(p).read_text(encoding="utf-8")
    hits = re.findall(r"https?://(?!127\.0\.0\.1|localhost)[^\s\"')]+", txt)
    if hits:
        print(f"  {p}: {hits}")
    else:
        print(f"  {p}: OK (no external URLs)")