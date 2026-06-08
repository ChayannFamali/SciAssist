"""RAG query engine — embed → retrieve → generate answer."""
from pydantic import BaseModel
from loguru import logger

from sciassist.config import get_settings
from sciassist.indexing.rag_indexer import RAGIndexer
from sciassist.router.model_router import ModelRouter
from sciassist.utils.lm_studio_client import LMStudioClient


class SourceChunk(BaseModel):
    citekey: str
    section: str
    score: float
    preview: str


class RAGAnswer(BaseModel):
    answer: str
    sources: list[SourceChunk]
    model: str


class QueryEngine:
    def __init__(self) -> None:
        self._indexer = RAGIndexer()
        self._llm = LMStudioClient()
        self._router = ModelRouter()
        self._system = self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        p = get_settings().project_root / "configs" / "prompts" / "rag_qa.md"
        return p.read_text(encoding="utf-8").strip() if p.exists() else (
            "Answer based ONLY on the context. Cite as [citekey]. "
            "If not found: 'Не могу подтвердить на основе библиотеки.'"
        )

    async def ask(
        self,
        question: str,
        top_k: int = 5,
        collection: str = "papers_full",
    ) -> RAGAnswer:
        # 1. Retrieve
        raw = await self._indexer.query(question, top_k=top_k, collection=collection)
        docs = raw.get("documents", [[]])[0]
        metas = raw.get("metadatas", [[]])[0]
        dists = raw.get("distances", [[]])[0]

        if not docs:
            return RAGAnswer(
                answer="Не могу подтвердить на основе библиотеки — индекс пуст.",
                sources=[],
                model="n/a",
            )

        # 2. Build context + source list
        ctx_parts: list[str] = []
        sources: list[SourceChunk] = []

        for doc, meta, dist in zip(docs, metas, dists):
            citekey = meta.get("citekey", "unknown")
            section = meta.get("section", "body")
            score = round(1.0 - float(dist), 3)
            ctx_parts.append(f"[{citekey}] (раздел: {section}):\n{doc}")
            sources.append(SourceChunk(
                citekey=citekey,
                section=section,
                score=score,
                preview=(doc[:200] + "…") if len(doc) > 200 else doc,
            ))

        context = "\n\n---\n\n".join(ctx_parts)

        # 3. Generate
        spec = self._router.select("chat")
        messages = [
            {"role": "system", "content": self._system},
            {"role": "user", "content": f"Контекст:\n\n{context}\n\nВопрос: {question}"},
        ]

        answer = await self._llm.chat(
            messages=messages,
            model=spec.name,
            temperature=spec.temperature,
            timeout=spec.timeout,
        )

        return RAGAnswer(answer=answer, sources=sources, model=spec.name)
