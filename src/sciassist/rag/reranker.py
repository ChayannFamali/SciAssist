"""LLM-based listwise reranker — переупорядочивает чанки по релевантности."""
import json
import re

from loguru import logger

from sciassist.router.model_router import ModelRouter
from sciassist.utils.lm_studio_client import LMStudioClient

_SYSTEM = (
    "You are a search relevance ranker for scientific papers. "
    "Given a question and numbered passages, return ONLY a JSON array of "
    "passage numbers, ordered from most to least relevant. "
    "Include ONLY passages that genuinely help answer the question — "
    "drop reference lists and irrelevant text. "
    'Example output: [3, 1, 5]. No explanations, no markdown.'
)

# сколько символов чанка показываем ранкеру (экономим контекст)
_SNIPPET = 500


def _parse_order(text: str, n: int) -> list[int]:
    """Извлекает массив индексов из ответа LLM. 1-based → 0-based."""
    m = re.search(r"$$[\d,\s]*$$", text)
    if not m:
        return list(range(n))
    try:
        order = json.loads(m.group(0))
    except Exception:
        return list(range(n))

    seen: set[int] = set()
    result: list[int] = []
    for x in order:
        try:
            i = int(x) - 1
        except (ValueError, TypeError):
            continue
        if 0 <= i < n and i not in seen:
            seen.add(i)
            result.append(i)
    return result or list(range(n))


class LLMReranker:
    def __init__(self) -> None:
        self._llm = LMStudioClient()
        self._spec = ModelRouter().select("rerank")

    async def rerank(self, question: str, docs: list[str]) -> list[int]:
        """Вернуть индексы docs, переупорядоченные по релевантности.
        Нерелевантные могут быть исключены (список короче входа)."""
        if len(docs) <= 1:
            return list(range(len(docs)))

        passages = "\n\n".join(f"[{i + 1}] {d[:_SNIPPET]}" for i, d in enumerate(docs))
        user = (
            f"Question: {question}\n\n"
            f"Passages:\n{passages}\n\n"
            f"Return a JSON array of passage numbers, most relevant first."
        )
        try:
            raw = await self._llm.chat(
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": f"/no_think\n{user}"},
                ],
                model=self._spec.name,
                temperature=self._spec.temperature,
                timeout=self._spec.timeout,
            )
            order = _parse_order(raw, len(docs))
        except Exception as e:
            logger.warning(f"Rerank упал ({e}); исходный порядок")
            return list(range(len(docs)))

        logger.debug(f"Rerank: {len(docs)} → порядок {order}")
        return order
