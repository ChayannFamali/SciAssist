"""ChromaDB indexer — papers_full and papers_notes collections."""
import hashlib
import json
import re
from pathlib import Path

import chromadb
from loguru import logger

from sciassist.config import get_settings, get_yaml_config
from sciassist.preprocessing.chunker import chunk_markdown
from sciassist.router.model_router import ModelRouter
from sciassist.utils.lm_studio_client import LMStudioClient

# Cosine distance: values in [0, 2], lower = more similar
# similarity = 1 - distance gives [-1, 1], typically [0, 1] for relevant docs
_COSINE = {"hnsw:space": "cosine"}

# спецтокены токенизатора/OCR
import re

_NOISE_RE = re.compile(r"<\s*/?\s*(?:EOS|BOS|PAD|UNK|s|pad|unk|mask)\s*>", re.IGNORECASE)
# YAML-frontmatter в начале: ---\n...\n---
_FRONTMATTER_RE = re.compile(r"^\s*---\s*\n.*?\n---\s*\n", re.DOTALL)


def _clean_chunk(text: str) -> str:
    """Вырезать frontmatter и спецтокены, схлопнуть пробелы."""
    text = _FRONTMATTER_RE.sub("", text)      # убрать YAML-блок, СОХРАНИВ контент
    text = _NOISE_RE.sub(" ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_junk(text: str) -> bool:
    """Отбросить только реально пустые/мусорные чанки (без startswith-фильтра!)."""
    if len(text) < 40:
        return True
    alnum = sum(c.isalnum() for c in text)
    if alnum < 25:                            # почти нет букв/цифр → шум
        return True
    return False



class RAGIndexer:
    """Manages ChromaDB. Always passes pre-computed embeddings — no default embedder."""

    def __init__(self) -> None:
        cfg = get_settings()
        self._llm = LMStudioClient()
        self._embed_model = ModelRouter().embed_model()

        db = chromadb.PersistentClient(path=str(cfg.chroma_db_path))
        # cosine distance — values in [0,2], similarity = 1 - distance ∈ [-1,1]
        self._papers = db.get_or_create_collection("papers_full", metadata=_COSINE)
        self._notes  = db.get_or_create_collection("papers_notes",  metadata=_COSINE)

        self._reg_path = cfg.project_root / "data" / "index_registry.json"
        self._reg_path.parent.mkdir(parents=True, exist_ok=True)
        self._reg: dict = (
            json.loads(self._reg_path.read_text()) if self._reg_path.exists() else {}
        )

    # ------------------------------------------------------------------
    def _md5(self, path: Path) -> str:
        return hashlib.md5(path.read_bytes()).hexdigest()

    def _save(self) -> None:
        self._reg_path.write_text(json.dumps(self._reg, indent=2))

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        yaml_cfg = get_yaml_config()
        batch: int = yaml_cfg.get("model_router", {}).get("embed", {}).get("max_batch", 32)
        out: list[list[float]] = []
        for i in range(0, len(texts), batch):
            out.extend(await self._llm.embed(texts[i : i + batch], model=self._embed_model))
        return out

    # ------------------------------------------------------------------
    async def index_paper(self, citekey: str, md_path: Path, *, force: bool = False) -> int:
        reg_key = f"paper:{citekey}"
        md5 = self._md5(md_path)

        if not force and self._reg.get(reg_key) == md5:
            logger.debug(f"{citekey}: без изменений, пропуск")
            return 0

        try:
            self._papers.delete(where={"citekey": citekey})
        except Exception:
            pass

        yaml_cfg = get_yaml_config()
        fc = yaml_cfg.get("chunking", {}).get("papers_full", {})
        chunks = chunk_markdown(
            md_path.read_text(encoding="utf-8"),
            chunk_size=fc.get("chunk_size", 1200),
            overlap=fc.get("chunk_overlap", 200),
        )

        if not chunks:
            logger.warning(f"{citekey}: нет чанков")
            return 0

        # Чистка + отсев мусорных чанков (выравниваем ids/metadatas)
        clean_chunks: list[tuple[str, object]] = []
        for c in chunks:
            cleaned = _clean_chunk(c.text)
            if _is_junk(cleaned):
                continue
            clean_chunks.append((cleaned, c))

        if not clean_chunks:
            logger.warning(f"{citekey}: после чистки не осталось чанков")
            return 0

        texts = [t for t, _ in clean_chunks]
        embeddings = await self._embed(texts)

        self._papers.add(
            ids=[f"{citekey}::p::{i}" for i in range(len(clean_chunks))],
            documents=texts,
            embeddings=embeddings,
            metadatas=[
                {"citekey": citekey, "section": c.section, "idx": c.chunk_index}
                for _, c in clean_chunks
            ],
        )
        logger.info(
            f"{citekey}: papers_full ← {len(clean_chunks)} чанков "
            f"(отброшено {len(chunks) - len(clean_chunks)})"
        )
        self._reg[reg_key] = md5
        self._save()
        return len(clean_chunks)

    async def index_note(self, citekey: str, note_path: Path, *, force: bool = False) -> int:
        reg_key = f"note:{citekey}"
        md5 = self._md5(note_path)
        if not force and self._reg.get(reg_key) == md5:
            return 0

        try:
            self._notes.delete(where={"citekey": citekey})
        except Exception:
            pass

        yaml_cfg = get_yaml_config()
        nc = yaml_cfg.get("chunking", {}).get("papers_notes", {})
        chunks = chunk_markdown(
            note_path.read_text(encoding="utf-8"),
            chunk_size=nc.get("chunk_size", 800),
            overlap=nc.get("chunk_overlap", 100),
        )
        if not chunks:
            return 0

        # Чистка + отсев мусорных чанков (фронтматтер заметок тоже отфильтруется)
        clean_chunks: list[tuple[str, object]] = []
        for c in chunks:
            cleaned = _clean_chunk(c.text)
            if _is_junk(cleaned):
                continue
            clean_chunks.append((cleaned, c))

        if not clean_chunks:
            logger.warning(f"{citekey}: после чистки не осталось чанков (notes)")
            return 0

        texts = [t for t, _ in clean_chunks]
        embeddings = await self._embed(texts)

        self._notes.add(
            ids=[f"{citekey}::n::{i}" for i in range(len(clean_chunks))],
            documents=texts,
            embeddings=embeddings,
            metadatas=[
                {"citekey": citekey, "section": c.section}
                for _, c in clean_chunks
            ],
        )
        logger.info(
            f"{citekey}: papers_notes ← {len(clean_chunks)} чанков "
            f"(отброшено {len(chunks) - len(clean_chunks)})"
        )
        self._reg[reg_key] = md5
        self._save()
        return len(clean_chunks)

    async def query(
        self,
        question: str,
        top_k: int = 5,
        collection: str = "papers_full",
        where: dict | None = None,
    ) -> dict:
        vec = await self._llm.embed([question], model=self._embed_model)
        col = self._papers if collection == "papers_full" else self._notes
        n = min(top_k, col.count() or 1)
        kwargs: dict = {"query_embeddings": [vec[0]], "n_results": n}
        if where:
            kwargs["where"] = where
        return col.query(**kwargs)

    def remove_paper(self, citekey: str) -> None:
        for col in (self._papers, self._notes):
            try:
                col.delete(where={"citekey": citekey})
            except Exception:
                pass
        self._reg.pop(f"paper:{citekey}", None)
        self._reg.pop(f"note:{citekey}", None)
        self._save()

    def stats(self) -> dict:
        return {
            "papers_full_chunks": self._papers.count(),
            "papers_notes_chunks": self._notes.count(),
            "indexed_papers": len([k for k in self._reg if k.startswith("paper:")]),
            "embed_model": self._embed_model,
        }
    def get_collection(self, name: str):
        """Вернуть Chroma-коллекцию по имени (для BM25)."""
        return self._papers if name == "papers_full" else self._notes
