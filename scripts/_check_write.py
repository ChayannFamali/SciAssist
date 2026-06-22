"""Smoke: write endpoints (link/tag/thought) on real notes.

Тестирует:
  1. Идемпотентность (link: changed=False если уже есть)
  2. mtime → 409 при расхождении
  3. NoteNotFound → 404
  4. Возврат mtime после записи (для следующей операции)
"""
import json
import urllib.request


BASE = "http://127.0.0.1:8776"
CK = "vaswani2023attention"


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


# 1. Сначала прочитаем заметку — узнаем mtime
code, note = hit("GET", f"/api/note/{CK}")
print(f"GET note: HTTP {code}, mtime={note.get('mtime')}, has [[chen2026memdreamer]]={'chen2026memdreamer' in note.get('links', [])}")
mtime = note.get("mtime")
links_before = note.get("links", [])

# 2. POST link с правильным mtime — должен быть changed=True (новый target)
target = "test_link_target_xyz"
code, r = hit("POST", f"/api/note/{CK}/link", {"target": target},
              headers={"If-Match-Mtime": str(mtime)})
print(f"POST link: HTTP {code}, {r}")

# 3. POST тот же link ещё раз (без mtime) — должен быть changed=False (идемпотентность)
code, r = hit("POST", f"/api/note/{CK}/link", {"target": target})
print(f"POST link (idempotent): HTTP {code}, {r}")

# 4. POST с устаревшим mtime — должен быть 409
code, r = hit("POST", f"/api/note/{CK}/link", {"target": "another"},
              headers={"If-Match-Mtime": str(1)})
print(f"POST link (mtime conflict): HTTP {code}, {r}")

# 5. POST link на несуществующую заметку — 404
code, r = hit("POST", "/api/note/nonexistent_xyz/link", {"target": "x"})
print(f"POST link (missing): HTTP {code}, {r}")

# 6. POST thought
code, r = hit("POST", f"/api/note/{CK}/thought", {"text": "Тестовая мысль из скрипта"})
print(f"POST thought: HTTP {code}, {r}")

# 7. POST tag
code, r = hit("POST", f"/api/note/{CK}/tag", {"tag": "test-script-tag"})
print(f"POST tag: HTTP {code}, {r}")

# 8. Удалить то что добавили (cleanup) — прямой rewrite файла
from pathlib import Path
note_path = Path(r"D:\SciVault\papers\@vaswani2023attention.md")
text = note_path.read_text(encoding="utf-8")
# Удалить тестовую строку ссылки
text = text.replace(f"- [[{target}]]\n", "")
# Удалить тестовую мысль (timestamp может отличаться — удаляем по подстроке)
import re
text = re.sub(r"- \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC: Тестовая мысль из скрипта\n", "", text)
# Удалить тестовый тег из YAML
text = text.replace("- test-script-tag\n", "")
note_path.write_text(text, encoding="utf-8")
print(f"\nCleanup: removed test link/thought/tag from {note_path}")