"""
Тест трёх локальных способов работы с Zotero — БЕЗ интернета, БЕЗ VPN.
"""
from pathlib import Path

# ========================================
# СПОСОБ 1: pyzotero в локальном режиме
# ========================================
print("=" * 60)
print("СПОСОБ 1: pyzotero local=True")
print("=" * 60)

try:
    from pyzotero import zotero
    # library_id=0 и api_key любой (при local=True не проверяется)
    zot = zotero.Zotero(library_id=0, library_type='user', local=True)
    items = zot.top(limit=5)
    print(f" Работает! Получено top items: {len(items)}")
    for item in items:
        title = item['data'].get('title', '?')
        citekey = item['data'].get('citationKey', '—')
        print(f"   [{citekey}] {title}")
except Exception as e:
    print(f" Не работает: {type(e).__name__}: {e}")

# ========================================
# СПОСОБ 2: прямой HTTP к локальному API
# ========================================
print("\n" + "=" * 60)
print("СПОСОБ 2: прямой HTTP к 127.0.0.1:23119")
print("=" * 60)

import httpx
try:
    r = httpx.get("http://127.0.0.1:23119/api/users/0/items?limit=3", timeout=5)
    if r.status_code == 200:
        data = r.json()
        print(f" Работает! Получено items: {len(data)}")
        for item in data:
            title = item['data'].get('title', '?')
            print(f"   {title}")
    else:
        print(f" HTTP {r.status_code}: {r.text[:200]}")
except Exception as e:
    print(f" Не работает: {type(e).__name__}: {e}")

# ========================================
# СПОСОБ 3: прямое чтение SQLite
# ========================================
print("\n" + "=" * 60)
print("СПОСОБ 3: прямое чтение zotero.sqlite")
print("=" * 60)

import sqlite3
import shutil
import tempfile

ZOTERO_DB = Path(r"D:\libraries\zotero.sqlite")  # подставь свой путь

if not ZOTERO_DB.exists():
    print(f" Файл не найден: {ZOTERO_DB}")
else:
    # Копируем БД во временный файл, чтобы не конфликтовать с запущенным Zotero
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    
    try:
        shutil.copy2(ZOTERO_DB, tmp_path)
        
        conn = sqlite3.connect(tmp_path)
        cur = conn.cursor()
        
        # Простой запрос: количество записей
        cur.execute("SELECT COUNT(*) FROM items WHERE itemTypeID != 14")  # 14 = attachment
        count = cur.fetchone()[0]
        print(f" Работает! Всего не-attachment items в БД: {count}")
        
        # Первые 5 top-level items с заголовками
        cur.execute("""
            SELECT i.key, iv.value
            FROM items i
            JOIN itemData id ON id.itemID = i.itemID
            JOIN itemDataValues iv ON iv.valueID = id.valueID
            JOIN fields f ON f.fieldID = id.fieldID
            WHERE f.fieldName = 'title'
              AND i.itemTypeID NOT IN (14, 1)  -- не attachment, не note
            LIMIT 5
        """)
        for key, title in cur.fetchall():
            print(f"   [{key}] {title}")
        
        conn.close()
    finally:
        tmp_path.unlink(missing_ok=True)
