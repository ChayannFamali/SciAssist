"""Smoke test for stage 1b."""
import json
import urllib.request


def hit(url):
    try:
        r = urllib.request.urlopen(url)
        body = r.read().decode()
        print(f"HTTP {r.status} {url}")
        try:
            d = json.loads(body)
            if isinstance(d, dict) and "nodes" in d:
                nodes = d["nodes"]
                edges = d["edges"]
                kinds = sorted({e["kind"] for e in edges})
                print(f"  nodes={len(nodes)} edges={len(edges)} kinds={kinds}")
                if nodes:
                    print(f"  first node: {nodes[0]}")
                if edges:
                    print(f"  first edge: {edges[0]}")
            else:
                print(json.dumps(d, ensure_ascii=False)[:600])
        except Exception:
            print(body[:300])
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} {url}")
        print(f"  body: {e.read().decode()[:300]}")
    print("---")


hit("http://127.0.0.1:8768/api/note/nope")
hit("http://127.0.0.1:8768/api/notes?keys=vaswani2023attention,chen2026memdreamer,seier2026modelling,nope")
hit("http://127.0.0.1:8768/api/graph?mode=links")
hit("http://127.0.0.1:8768/api/graph?mode=overlay")
hit("http://127.0.0.1:8768/api/graph?mode=overlay&refresh=1")