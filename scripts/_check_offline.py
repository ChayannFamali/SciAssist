"""TX.2 offline: проверяем что фронт работает с выключенным интернетом.

Стратегия: проверяем что все <script src> и <link href> указывают
на /vendor/* или локальные пути. Никаких http(s):// в src/href.
"""
import re
import pathlib


root = pathlib.Path(r"H:\SciAssist\web\static")
violations = []
external_assets = []

for path in root.rglob("*"):
    if path.is_file() and path.suffix in {".html", ".css", ".js"}:
        text = path.read_text(encoding="utf-8")
        # Ищем src= или href= с внешними URL
        for m in re.finditer(r'(src|href)\s*=\s*["\']([^"\']+)["\']', text):
            url = m.group(2)
            if url.startswith("http://") or url.startswith("https://"):
                violations.append((path, url))
                # исключаем localhost/127.0.0.1
                if not any(x in url for x in ["127.0.0.1", "localhost"]):
                    external_assets.append((path, url))

# Также fetch() внутри JS не должен ходить в чужие домены
fetch_violations = []
for path in (root / "app.js").glob("*"):
    if path.suffix == ".js":
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r'fetch\s*\(\s*["\']([^"\']+)["\']', text):
            url = m.group(1)
            if url.startswith("http://") or url.startswith("https://"):
                fetch_violations.append((path, url))

# Также import/dynamic import
import_violations = []
for path in (root / "app.js").glob("*"):
    if path.suffix == ".js":
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r'import\s+.*from\s+["\']([^"\']+)["\']', text):
            url = m.group(1)
            if url.startswith("http://") or url.startswith("https://"):
                import_violations.append((path, url))

print(f"=== Offline check ({root}) ===")
print()
print(f"HTML/CSS/JS external URLs: {len(violations)}")
for p, u in violations:
    print(f"  {p.name}: {u}")
print()
print(f"fetch() external: {len(fetch_violations)}")
for p, u in fetch_violations:
    print(f"  {p.name}: {u}")
print()
print(f"import external: {len(import_violations)}")
for p, u in import_violations:
    print(f"  {p.name}: {u}")
print()
if not (violations or fetch_violations or import_violations):
    print("✅ OFFLINE READY: нет внешних URL в src/href/fetch/import")
else:
    print("❌ Внешние зависимости найдены")

# Также проверяем что vendor/ содержит ожидаемые библиотеки
vendor = root / "vendor"
print()
print(f"=== Vendor ({vendor}) ===")
for f in sorted(vendor.glob("*")):
    if f.is_file():
        print(f"  {f.name}: {f.stat().st_size // 1024} KB")