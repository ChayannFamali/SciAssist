"""
Zotero client — три бэкенда, автовыбор.
HTTPBackend (pyzotero local) → SQLiteBackend (read-only fallback).
BBTBackend — вспомогательный, только для citekey-резолвинга.
"""
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
from loguru import logger
from pydantic import BaseModel
from pyzotero import zotero as pyzotero_lib

from sciassist.config import get_settings, get_yaml_config
from sciassist.exceptions import ZoteroBackendError


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
# Типы которые SciAssist умеет обрабатывать
_PAPER_TYPES = {
        "journalArticle", "preprint", "conferencePaper",
        "book", "bookSection", "thesis", "report", "manuscript", "document",
    }
class Author(BaseModel):
    first: str = ""
    last: str = ""

    def __str__(self) -> str:
        return f"{self.first} {self.last}".strip()


class ZoteroItem(BaseModel):
    key: str
    citekey: str = ""
    item_type: str = ""
    title: str = ""
    authors: list[Author] = []
    year: int | None = None
    doi: str | None = None
    abstract: str | None = None
    tags: list[str] = []
    collections: list[str] = []
    date_added: datetime
    date_modified: datetime


def _dt(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime(2000, 1, 1, tzinfo=timezone.utc)


def _parse_item(raw: dict) -> ZoteroItem:
    """Parse a raw pyzotero/HTTP response dict into ZoteroItem."""
    data = raw.get("data", raw)
    key = data.get("key") or raw.get("key", "")

    authors: list[Author] = []
    for c in data.get("creators", []):
        if c.get("creatorType") == "author":
            authors.append(Author(
                first=c.get("firstName", ""),
                last=c.get("lastName") or c.get("name", ""),
            ))

    year: int | None = None
    for token in str(data.get("date", "")).replace("/", " ").split():
        try:
            y = int(token[:4])
            if 1900 <= y <= 2100:
                year = y
                break
        except ValueError:
            pass

    return ZoteroItem(
        key=key,
        citekey=data.get("citationKey", ""),
        item_type=data.get("itemType", ""),
        title=data.get("title", ""),
        authors=authors,
        year=year,
        doi=data.get("DOI") or data.get("doi"),
        abstract=data.get("abstractNote"),
        tags=[t["tag"] for t in data.get("tags", []) if isinstance(t, dict)],
        collections=data.get("collections", []),
        date_added=_dt(data.get("dateAdded", "")),
        date_modified=_dt(data.get("dateModified", "")),
    )


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class HTTPBackend:
    """Full read/write via pyzotero local=True (Zotero must be running)."""

    def __init__(self) -> None:
        self._zot = pyzotero_lib.Zotero(library_id=0, library_type="user", local=True)
        self._url = get_settings().zotero_local_url

    def ping(self) -> bool:
        try:
            r = httpx.get(f"{self._url}/api/users/0/items?limit=1", timeout=3.0)
            return r.status_code == 200
        except Exception:
            return False

    def _collection_key(self, name: str) -> str | None:
        for c in self._zot.collections():
            if c["data"]["name"] == name:
                return c["key"]
        return None

    def get_collection_names(self) -> list[str]:
        return [c["data"]["name"] for c in self._zot.collections()]

    def get_items_in_collection(self, name: str) -> list[dict]:
        key = self._collection_key(name)
        if not key:
            return []
        all_items = self._zot.collection_items(key)
        filtered = [i for i in all_items if i["data"].get("itemType") in _PAPER_TYPES]
        skipped = len(all_items) - len(filtered)
        if skipped:
            logger.debug(f"Collection '{name}': пропущено {skipped} не-статей (attachments, snapshots и т.п.)")
        return filtered



    def get_items_by_tag(self, tag: str) -> list[dict]:
        return self._zot.items(tag=tag)

    def get_item(self, key: str) -> dict:
        return self._zot.item(key)

    def get_children(self, key: str) -> list[dict]:
        return self._zot.children(key)

    def add_tag(self, key: str, tag: str) -> None:
        item = self._zot.item(key)
        tags = item["data"].get("tags", [])
        if not any(t["tag"] == tag for t in tags):
            tags.append({"tag": tag})
            item["data"]["tags"] = tags
            self._zot.update_item(item)

    def move_to_collection(self, key: str, name: str) -> None:
        col_key = self._collection_key(name)
        if col_key is None:
            logger.warning(f"Collection '{name}' not found")
            return
        self._zot.addto_collection(col_key, self._zot.item(key))

    def create_collection(self, name: str) -> None:
        self._zot.create_collections([{"name": name, "parentCollection": False}])
        logger.info(f"Created Zotero collection: '{name}'")

    def find_by_citekey(self, citekey: str) -> dict | None:
        """Сканирует items в поиске совпадения по citationKey."""
        for item in self._zot.items(q=citekey[:15]):   # narrowed search first
            if item["data"].get("citationKey") == citekey:
                return item
        for item in self._zot.everything(self._zot.items()):   # full scan fallback
            if item["data"].get("citationKey") == citekey:
                return item
        return None


class SQLiteBackend:
    """Read-only fallback: copies zotero.sqlite to temp before reading."""

    def __init__(self) -> None:
        self._db = get_settings().zotero_data_dir / "zotero.sqlite"

    def ping(self) -> bool:
        return self._db.exists()

    def _conn(self) -> tuple[sqlite3.Connection, Path]:
        tmp = Path(tempfile.mktemp(suffix=".sqlite"))
        shutil.copy2(self._db, tmp)
        conn = sqlite3.connect(tmp)
        conn.row_factory = sqlite3.Row
        return conn, tmp

    def count_items(self) -> int:
        conn, tmp = self._conn()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM items WHERE itemTypeID NOT IN (1, 14)"
            ).fetchone()[0]
        finally:
            conn.close(); tmp.unlink(missing_ok=True)

    def get_items_in_collection(self, name: str) -> list[dict]:
        conn, tmp = self._conn()
        try:
            rows = conn.execute("""
                SELECT i.key FROM items i
                JOIN collectionItems ci ON ci.itemID = i.itemID
                JOIN collections c ON c.collectionID = ci.collectionID
                WHERE c.collectionName = ? AND i.itemTypeID NOT IN (1, 14)
            """, (name,)).fetchall()
            return [{"key": r["key"], "data": {"key": r["key"]}} for r in rows]
        finally:
            conn.close(); tmp.unlink(missing_ok=True)


class BBTBackend:
    """Better BibTeX JSON-RPC — supplementary, citekey resolution only."""

    def __init__(self) -> None:
        self._url = f"{get_settings().zotero_local_url}/better-bibtex/json-rpc"

    def ping(self) -> bool:
        try:
            r = httpx.post(
                self._url,
                json={"jsonrpc": "2.0", "method": "item.search", "params": ["x"], "id": 1},
                timeout=3.0,
            )
            return r.status_code in (200, 400)
        except Exception:
            return False

    def get_citekey(self, item_key: str) -> str | None:
        try:
            r = httpx.post(
                self._url,
                json={"jsonrpc": "2.0", "method": "item.citationKey",
                      "params": [item_key], "id": 1},
                timeout=5.0,
            )
            if r.status_code == 200:
                result = r.json().get("result")
                return result if isinstance(result, str) else None
        except Exception:
            pass
        return None

    def search_by_citekey(self, citekey: str) -> str | None:
        """Возвращает Zotero item key по citekey через BBT JSON-RPC."""
        try:
            r = httpx.post(
                self._url,
                json={"jsonrpc": "2.0", "method": "item.search", "params": [citekey], "id": 1},
                timeout=5.0,
            )
            if r.status_code == 200:
                for item in r.json().get("result", []):
                    if isinstance(item, dict):
                        if item.get("citationKey") == citekey or item.get("citekey") == citekey:
                            return item.get("itemKey") or item.get("key")
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------

class ZoteroClient:
    """
    Unified Zotero access.
    Auto-selects HTTP → SQLite; BBT used for citekey fallback.
    """

    def __init__(self) -> None:
        self._http = HTTPBackend()
        self._sqlite = SQLiteBackend()
        self._bbt = BBTBackend()
        self._storage = get_settings().zotero_storage_dir

        if self._http.ping():
            self.backend: HTTPBackend | SQLiteBackend = self._http
            self._backend_name = "http"
            logger.info("ZoteroClient: HTTP backend (pyzotero local)")
        elif self._sqlite.ping():
            self.backend = self._sqlite
            self._backend_name = "sqlite"
            logger.warning("ZoteroClient: SQLite fallback (read-only) — запусти Zotero для полного функционала")
        else:
            raise ZoteroBackendError(
                "Zotero недоступен: HTTP не отвечает, SQLite не найден.\n"
                "Запусти Zotero или проверь ZOTERO_DATA_DIR в .env"
            )

    # ------------------------------------------------------------------

    def health_check(self) -> dict:
        return {
            "backend": self._backend_name,
            "http": self._http.ping(),
            "sqlite": self._sqlite.ping(),
            "bbt": self._bbt.ping(),
            "write": self._backend_name == "http",
        }

    def get_items_in_collection(self, name: str) -> list[ZoteroItem]:
        return [_parse_item(r) for r in self.backend.get_items_in_collection(name)]

    def get_items_by_tag(self, tag: str) -> list[ZoteroItem]:
        if not isinstance(self.backend, HTTPBackend):
            logger.warning("get_items_by_tag: требуется HTTP backend")
            return []
        return [_parse_item(r) for r in self.backend.get_items_by_tag(tag)]

    def get_item(self, key: str) -> ZoteroItem:
        if not isinstance(self.backend, HTTPBackend):
            raise ZoteroBackendError("get_item требует HTTP backend (Zotero должен быть запущен)")
        return _parse_item(self.backend.get_item(key))

    def get_citekey(self, key: str) -> str:
        """Native citationKey field → BBT JSON-RPC fallback → item key."""
        if isinstance(self.backend, HTTPBackend):
            try:
                raw = self.backend.get_item(key)
                ck = raw.get("data", {}).get("citationKey", "")
                if ck:
                    return ck
            except Exception:
                pass

        ck = self._bbt.get_citekey(key)
        if ck:
            return ck

        logger.warning(f"citekey не найден для {key}, используем item key как fallback")
        return key

    def find_by_citekey(self, citekey: str) -> ZoteroItem | None:
        """Найти статью по citation key. BBT → pyzotero scan."""
        # Быстрый путь через BBT
        if self._bbt.ping():
            item_key = self._bbt.search_by_citekey(citekey)
            if item_key and isinstance(self.backend, HTTPBackend):
                try:
                    return _parse_item(self.backend.get_item(item_key))
                except Exception:
                    pass

        # Fallback: полный скан
        if isinstance(self.backend, HTTPBackend):
            raw = self.backend.find_by_citekey(citekey)
            return _parse_item(raw) if raw else None

        logger.warning("find_by_citekey: требует HTTP backend")
        return None

    def get_pdf_path(self, key: str) -> Path | None:
        """Find local PDF file for a Zotero item."""
        if not isinstance(self.backend, HTTPBackend):
            logger.warning("get_pdf_path требует HTTP backend")
            return None

        try:
            children = self.backend.get_children(key)
        except Exception as e:
            logger.warning(f"Не удалось получить children для {key}: {e}")
            return None

        for child in children:
            data = child.get("data", {})
            if data.get("contentType") != "application/pdf":
                continue

            link_mode = data.get("linkMode", "")

            if link_mode in ("imported_file", "imported_url"):
                att_key = child["key"]
                filename = data.get("filename", "")
                if filename:
                    path = self._storage / att_key / filename
                    if path.exists():
                        return path
                    logger.warning(f"PDF ожидался по пути {path}, но не найден")

            elif link_mode == "linked_file":
                raw_path = data.get("path", "")
                if raw_path and not raw_path.startswith("attachments:"):
                    path = Path(raw_path)
                    if path.exists():
                        return path

        logger.warning(f"PDF не найден для item {key}")
        return None

    def add_tag(self, key: str, tag: str) -> None:
        if not isinstance(self.backend, HTTPBackend):
            logger.warning("add_tag требует HTTP backend")
            return
        self.backend.add_tag(key, tag)

    def move_to_collection(self, key: str, collection_name: str) -> None:
        if not isinstance(self.backend, HTTPBackend):
            logger.warning("move_to_collection требует HTTP backend")
            return
        self.backend.move_to_collection(key, collection_name)

    def ensure_collections(self) -> None:
        """Create Queue/Processed if they don't exist (HTTP only)."""
        if not isinstance(self.backend, HTTPBackend):
            logger.warning("ensure_collections требует HTTP backend")
            return

        yaml_cfg = get_yaml_config().get("zotero", {})
        needed = [
            yaml_cfg.get("queue_collection", "SciAssist Queue"),
            yaml_cfg.get("processed_collection", "SciAssist Processed"),
        ]
        existing = self.backend.get_collection_names()
        for name in needed:
            if name not in existing:
                self.backend.create_collection(name)
            else:
                logger.debug(f"Коллекция уже существует: '{name}'")

    def get_collection_names(self) -> list[str]:
        if isinstance(self.backend, HTTPBackend):
            return self.backend.get_collection_names()
        return []
