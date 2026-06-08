"""RAG query engine — embed → retrieve → filter → generate answer."""
from collections import defaultdict

from pydantic import BaseModel
from loguru import logger

from sciassist.config import get_settings
from sciassist.indexing.rag_indexer import RAGIndexer
from sciassist.router.model_router import ModelRouter
from sciassist.utils.lm_studio_client import LMStudioClient

_DEFAULT_MIN_SCORE = 0.4
_DEFAULT_MAX_PER_PAPER = 3
_SECTION_DENYLIST = {"references", "bibliography", "acknowledgments", "acknowledgements"}


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
        self._reranker = None
        self._bm25_cache: dict = {}

    def _load_system_prompt(self) -> str:
        p = get_settings().project_root / "configs" / "prompts" / "rag_qa.md"
        return p.read_text(encoding="utf-8").strip() if p.exists() else (
            "Answer based ONLY on the context. Cite as [citekey]. "
            "If not found: 'Не могу подтвердить на основе библиотеки.'"
        )

    # ------------------------------------------------------------------
    # BM25 helper
    # ------------------------------------------------------------------
    def _bm25(self, collection: str):
        if collection not in self._bm25_cache:
            from sciassist.rag.hybrid_search import BM25Index
            col = self._indexer.get_collection(collection)
            self._bm25_cache[collection] = BM25Index(col)
        return self._bm25_cache[collection]

    # ------------------------------------------------------------------
    # Честное чередование двух списков (papers_full + papers_notes)
    # ------------------------------------------------------------------
    @staticmethod
    def _interleave(a: list, b: list) -> list:
        out, i, j = [], 0, 0
        while i < len(a) or j < len(b):
            if i < len(a):
                out.append(a[i]); i += 1
            if j < len(b):
                out.append(b[j]); j += 1
        return out

    # ------------------------------------------------------------------
    # HyDE: генерируем гипотетический абзац для dense-запроса
    # ------------------------------------------------------------------
    async def _hyde(self, question: str) -> str:
        spec = self._router.select("chat")
        prompt = (
            "Напиши короткий (3–4 предложения) гипотетический абзац из научной "
            "статьи, который бы отвечал на вопрос. Только текст абзаца.\n\n"
            f"Вопрос: {question}"
        )
        try:
            hypo = await self._llm.chat(
                messages=[{"role": "user", "content": f"/no_think\n{prompt}"}],
                model=spec.name, temperature=0.3, timeout=spec.timeout, max_tokens=300,
            )
            return f"{question}\n{hypo}".strip()   # комбинируем — устойчивее
        except Exception as e:
            logger.warning(f"HyDE упал ({e}); используем исходный вопрос")
            return question

    # ------------------------------------------------------------------
    # Гибридный retrieve: dense (HyDE?) + sparse (BM25, исходный вопрос) → RRF
    # Возвращает кортежи (doc, meta, cos | None, in_sparse: bool)
    # ------------------------------------------------------------------
    async def _retrieve(self, dense_query: str, sparse_query: str, top_k: int,
                        collection: str, hybrid: bool):
        raw = await self._indexer.query(dense_query, top_k=top_k * 4, collection=collection)
        ids   = raw.get("ids",       [[]])[0]
        docs  = raw.get("documents", [[]])[0]
        metas = raw.get("metadatas", [[]])[0]
        dists = raw.get("distances", [[]])[0]

        dense: dict = {}
        for rank, (i, d, m, dist) in enumerate(zip(ids, docs, metas, dists)):
            dense[i] = {"doc": d, "meta": m, "cos": round(1.0 - float(dist), 3), "drank": rank}
        best_cos = max((v["cos"] for v in dense.values()), default=0.0)

        if not hybrid:
            return [(v["doc"], v["meta"], v["cos"], False) for v in dense.values()], best_cos

        sparse: dict = {}
        for rank, (i, d, m, _sc) in enumerate(
            self._bm25(collection).search(sparse_query, top_k * 4)  # ← исходный вопрос
        ):
            sparse[i] = {"doc": d, "meta": m, "srank": rank}

        K = 60
        fused = []
        for i in set(dense) | set(sparse):
            score = 0.0
            if i in dense:  score += 1.0 / (K + dense[i]["drank"])
            if i in sparse: score += 1.0 / (K + sparse[i]["srank"])
            src = dense.get(i) or sparse.get(i)
            cos = dense[i]["cos"] if i in dense else None
            fused.append((score, src["doc"], src["meta"], cos, i in sparse))

        fused.sort(key=lambda x: x[0], reverse=True)
        candidates = [(d, m, c, s) for _s, d, m, c, s in fused]
        logger.info(f"Hybrid: dense={len(dense)}, sparse={len(sparse)}, best_cos={best_cos}")
        return candidates, best_cos

    # ------------------------------------------------------------------
    # Публичный метод
    # ------------------------------------------------------------------
    async def ask(
        self,
        question: str,
        top_k: int = 5,
        collection: str = "papers_full",
        min_score: float = _DEFAULT_MIN_SCORE,
        max_per_paper: int = _DEFAULT_MAX_PER_PAPER,
        rerank: bool = True,
        hybrid: bool = True,
        hyde: bool = False,                                    # ← добавлено
    ) -> RAGAnswer:
        # 1. Формируем запросы: dense может быть расширен через HyDE,
        #    sparse всегда исходный вопрос (BM25 плохо работает с гипотезами)
        dense_q  = await self._hyde(question) if hyde else question
        sparse_q = question

        if hyde and dense_q != question:
            logger.info(f"HyDE: dense-запрос расширен ({len(dense_q)} симв.)")

        # 2. Retrieve — одна коллекция или честное чередование двух
        if collection == "both":
            cf, best_f = await self._retrieve(dense_q, sparse_q, top_k, "papers_full",  hybrid)
            cn, best_n = await self._retrieve(dense_q, sparse_q, top_k, "papers_notes", hybrid)
            candidates = self._interleave(cf, cn)
            best_cos   = max(best_f, best_n)
        else:
            candidates, best_cos = await self._retrieve(dense_q, sparse_q, top_k, collection, hybrid)

        if not candidates:
            return RAGAnswer(
                answer="Не могу подтвердить на основе библиотеки — индекс пуст.",
                sources=[], model="n/a",
            )

        # 3. Порог (косинус) + денилист секций.
        #    BM25-хиты (in_sparse=True) обходят косинусный порог.
        filtered: list[tuple[str, dict, float]] = []
        dropped_section = 0
        for doc, meta, cos, in_sparse in candidates:
            section = (meta.get("section") or "").strip().lower()
            if section in _SECTION_DENYLIST:
                dropped_section += 1
                continue
            if not in_sparse and cos is not None and cos < min_score:
                continue
            filtered.append((doc, meta, cos if cos is not None else 0.0))

        if dropped_section:
            logger.debug(f"Отсеяно {dropped_section} чанков из мусорных секций")

        if not filtered:
            logger.info(
                f"Нет релевантных чанков (лучший cos: {best_cos}). Запрос: {question!r}"
            )
            return RAGAnswer(
                answer=(
                    f"Не могу подтвердить на основе библиотеки — "
                    f"нет фрагментов выше порога (лучший cos: {best_cos}). "
                    f"Переформулируй вопрос или снизь --min-score."
                ),
                sources=[], model="n/a",
            )

        # 4. Re-ranking
        if rerank and len(filtered) > 1:
            if self._reranker is None:
                from sciassist.rag.reranker import LLMReranker
                self._reranker = LLMReranker()
            order = await self._reranker.rerank(question, [c[0] for c in filtered])
            filtered = [filtered[i] for i in order]

        # 5. Дедуп по citekey + срез до top_k
        kept: list[tuple[str, dict, float]] = []
        per_paper: dict[str, int] = defaultdict(int)
        for doc, meta, score in filtered:
            ck = meta.get("citekey", "unknown")
            if per_paper[ck] >= max_per_paper:
                continue
            per_paper[ck] += 1
            kept.append((doc, meta, score))
            if len(kept) >= top_k:
                break

        # 6. Контекст + источники
        ctx_parts: list[str] = []
        sources: list[SourceChunk] = []
        for doc, meta, score in kept:
            citekey = meta.get("citekey", "unknown")
            section = meta.get("section", "body")
            ctx_parts.append(f"[{citekey}] (раздел: {section}):\n{doc}")
            sources.append(SourceChunk(
                citekey=citekey, section=section, score=score,
                preview=(doc[:200] + "…") if len(doc) > 200 else doc,
            ))
        context = "\n\n---\n\n".join(ctx_parts)

        # 7. Generate
        spec = self._router.select("chat")
        answer = await self._llm.chat(
            messages=[
                {"role": "system", "content": self._system},
                {"role": "user", "content": f"Контекст:\n\n{context}\n\nВопрос: {question}"},
            ],
            model=spec.name, temperature=spec.temperature, timeout=spec.timeout,
        )
        return RAGAnswer(answer=answer, sources=sources, model=spec.name)
