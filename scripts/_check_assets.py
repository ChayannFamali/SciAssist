"""Verify served asset sizes."""
import urllib.request

for url in [
    "http://127.0.0.1:8000/vendor/cytoscape.min.js",
    "http://127.0.0.1:8000/vendor/markdown-it.min.js",
    "http://127.0.0.1:8000/app.js",
    "http://127.0.0.1:8000/style.css",
    "http://127.0.0.1:8000/api/graph?mode=overlay",
]:
    try:
        r = urllib.request.urlopen(url, timeout=5)
        body = r.read()
        print(f"{r.status} {len(body):8} {url}")
    except Exception as e:
        print(f"ERR  {url}: {e}")