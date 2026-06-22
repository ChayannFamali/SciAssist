"""Verify TX.1: все ручки возвращают единый формат {"error": {...}}."""
import json
import urllib.request


BASE = "http://127.0.0.1:8777"


def hit(method, path, body=None, headers=None):
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=h, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=5)
        body = r.read().decode()
        try:
            d = json.loads(body)
        except Exception:
            d = body
        return r.status, d
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            d = json.loads(body)
        except Exception:
            d = body
        return e.code, d


print("=== Ошибки должны быть в формате {error: {code, type, message, path}} ===")
print()

# 404 — нота не найдена
code, d = hit("GET", "/api/note/nonexistent_xyz")
print(f"404 (note missing):    HTTP {code}  {d}")

# 404 — job не найден
code, d = hit("GET", "/api/jobs/nonexistent_xyz")
print(f"404 (job missing):     HTTP {code}  {d}")

# 422 — пустой body в POST ask
code, d = hit("POST", "/api/ask", {})
print(f"422 (validation):      HTTP {code}  {d}")

# 422 — пустой body в POST process
code, d = hit("POST", "/api/process", {})
print(f"422 (validation):      HTTP {code}  {d}")

# 400 — empty citekey в process
code, d = hit("POST", "/api/process", {"citekey": "   "})
print(f"400 (empty citekey):   HTTP {code}  {d}")

# 400 — bad collection
code, d = hit("GET", "/api/search?q=test&col=invalid")
print(f"400 (bad collection):  HTTP {code}  {d}")

# 503 — пропускаем (LM Studio работает; ask занимает ~20с, это OK)
print(f"503 (ask slow):        skipped — LM Studio поднят, ask=~20с OK")

# 409 — mtime conflict
# сначала получим mtime
code, note = hit("GET", "/api/note/vaswani2023attention")
if code == 200:
    mtime = note["mtime"]
    code, d = hit("POST", "/api/note/vaswani2023attention/link",
                  {"target": "new_test_xyz"},
                  headers={"If-Match-Mtime": str(1)})
    print(f"409 (mtime conflict):  HTTP {code}  {d}")

# 200 — успех
code, d = hit("GET", "/api/health")
print(f"200 (health):          HTTP {code}  {d}")

# 404 — статика отсутствует
code, d = hit("GET", "/nonexistent.html")
print(f"404 (static missing):  HTTP {code}  {d[:200] if isinstance(d, str) else d}")