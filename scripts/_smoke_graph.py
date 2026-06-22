"""Smoke test for graph endpoints."""
import json
import time
import urllib.request


def hit(url):
    t = time.time()
    try:
        r = urllib.request.urlopen(url, timeout=60)
        body = r.read().decode()
        dt = time.time() - t
        d = json.loads(body)
        kinds = sorted({e["kind"] for e in d.get("edges", [])})
        print(f"HTTP {r.status} ({dt:.2f}s) {url}")
        print(f"  nodes={len(d.get('nodes', []))} edges={len(d.get('edges', []))} kinds={kinds}")
        if d.get("edges"):
            print(f"  first: {d['edges'][0]}")
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} {url}: {e.read().decode()[:200]}")
    except Exception as e:
        print(f"ERR {url}: {e}")
    print("---")


hit("http://127.0.0.1:8769/api/graph?mode=links")
hit("http://127.0.0.1:8769/api/graph?mode=semantic")
hit("http://127.0.0.1:8769/api/graph?mode=overlay")
hit("http://127.0.0.1:8769/api/graph?mode=overlay&refresh=1")
hit("http://127.0.0.1:8769/api/graph?mode=overlay&threshold=0.7&top_k=3")