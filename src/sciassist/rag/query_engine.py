"""RAG query engine — embed → retrieve → filter → generate answer."""
import re
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
    text: str = ""          # полный чанк (preview — обрезок для UI; eval матчит по text)


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
    # RU→EN расширение sparse-запроса: BM25 RU-лексики в EN-корпусе = 0
    # lexical-хитов (структурный разрыв языков: вопросы RU, статьи EN;
    # зеркальная физика structure-перевода). EN-строка ДОБАВЛЯЕТСЯ к вопросу —
    # RU-токены остаются для RU-корпуса (учебники). Роль rerank = lfm2.5:
    # интерактивный MoE, temp 0, мгновенный.
    # ------------------------------------------------------------------
    async def _expand_en(self, question: str) -> str:
        spec = self._router.select("rerank")
        try:
            en = await self._llm.chat(
                messages=[{"role": "user", "content":
                    "Translate this question into English for scientific literature search. "
                    "Return ONLY the English translation, one line, nothing else.\n\n"
                    f"Question: {question}"}],
                model=spec.name, temperature=spec.temperature, timeout=spec.timeout,
                max_tokens=200,
            )
            en = (en or "").strip()
            return en if re.search(r"[A-Za-z]", en) else ""
        except Exception as e:
            logger.warning(f"expand_en упал ({e}); sparse = исходный вопрос")
            return ""

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
        hyde: bool = True,
        expand_en: bool = True,
        pool_k: int = 10,                                       # пул кандидатов на rerank
        generate: bool = True,                                 # False = только retrieval (eval)
        on_token: "callable | None" = None,                   # стрим ответа в CLI
        exclude_citekey: str | None = None,                   # консульт критики: убрать свои чанки
    ) -> RAGAnswer:
        # 1. dense: HyDE-гипотеза; sparse: вопрос + EN-перевод (разрыв языков
        #    RU-вопрос / EN-корпус; BM25 гипотезами не кормим — исходный вопрос)
        dense_q = await self._hyde(question) if hyde else question
        sparse_q = question
        if expand_en and re.search(r"[А-Яа-яЁё]", question):
            en = await self._expand_en(question)
            if en:
                sparse_q = f"{question}\n{en}"
                logger.info(f"expand_en: sparse +{len(en)} симв. EN")

        if hyde and dense_q != question:
            logger.info(f"HyDE: dense-запрос расширен ({len(dense_q)} симв.)")

        # 2. Retrieve — пул pool_k (top_k остаётся размером контекста)
        if collection == "both":
            cf, best_f = await self._retrieve(dense_q, sparse_q, pool_k, "papers_full",  hybrid)
            cn, best_n = await self._retrieve(dense_q, sparse_q, pool_k, "papers_notes", hybrid)
            candidates = self._interleave(cf, cn)
            best_cos   = max(best_f, best_n)
        elif collection == "all":
            cf, best_f = await self._retrieve(dense_q, sparse_q, pool_k, "papers_full",  hybrid)
            cn, best_n = await self._retrieve(dense_q, sparse_q, pool_k, "papers_notes", hybrid)
            ct, best_t = await self._retrieve(dense_q, sparse_q, pool_k, "textbooks",     hybrid)
            candidates = self._interleave(self._interleave(cf, cn), ct)
            best_cos   = max(best_f, best_n, best_t)
        else:
            # papers_full | papers_notes | textbooks
            candidates, best_cos = await self._retrieve(dense_q, sparse_q, pool_k, collection, hybrid)

        if not candidates:
            return RAGAnswer(
                answer="Не могу подтвердить на основе библиотеки — индекс пуст.",
                sources=[], model="n/a",
            )

        # 2.5. Исключение собственных чанков (консульт критики: статья не
        #      консультирует сама себя) — до порога/реранка, чтобы не ели бюджет
        if exclude_citekey:
            n0 = len(candidates)
            candidates = [c for c in candidates if c[1].get("citekey") != exclude_citekey]
            if n0 - len(candidates):
                logger.info(f"exclude_citekey={exclude_citekey}: убрано {n0 - len(candidates)} своих чанков")

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
                text=doc,
            ))
        context = "\n\n---\n\n".join(ctx_parts)

        if not generate:
            return RAGAnswer(answer="", sources=sources, model="retrieval-only")

        # 7. Generate
        spec = self._router.select("chat")
        answer = await self._llm.chat(
            messages=[
                {"role": "system", "content": self._system},
                {"role": "user", "content": f"Контекст:\n\n{context}\n\nВопрос: {question}"},
            ],
            model=spec.name, temperature=spec.temperature, timeout=spec.timeout,
            on_token=on_token,
        )

        # 8. Cite-чек: ответ может цитировать источники, которых нет в выдаче
        # ([x] не из чанков = галлюцинация источника; [[wikilink]] не считаем)
        valid_cks = {s.citekey for s in sources}
        cited = set(re.findall(r"(?<!\[)\[([a-z0-9][a-z0-9_\-.]{3,})\](?!\])", answer.lower()))
        unknown = {c for c in cited if c not in valid_cks}
        if unknown:
            logger.warning(f"ask: ответ цитирует источники вне выдачи RAG: {sorted(unknown)}")
            answer += (
                "\n\n> [!warning] ⚠ Часть процитированных источников не из выдачи RAG "
                f"({', '.join(sorted(unknown))}) — возможно, галлюцинация."
            )

        return RAGAnswer(answer=answer, sources=sources, model=spec.name)
