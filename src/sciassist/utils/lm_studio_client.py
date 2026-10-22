"""Async client for LM Studio (OpenAI-compatible API)."""
import asyncio
import base64
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger
from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, BadRequestError

from sciassist.config import get_settings
from sciassist.exceptions import LMStudioError


class LMStudioClient:
    """Async wrapper around LM Studio's OpenAI-compatible API."""

    def __init__(self) -> None:
        cfg = get_settings()
        self._client = AsyncOpenAI(
            base_url=cfg.lm_studio_url,
            api_key="lm-studio",  # ignored by LM Studio
        )
        self._cfg = cfg
        self._log_path = cfg.logs_path / "llm_calls.jsonl"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _log(
        self,
        *,
        model: str,
        task: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        duration_ms: float,
        error: str | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "task": task,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "duration_ms": round(duration_ms),
        }
        if error:
            record["error"] = error
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    async def _retry(self, factory: Any, *, task: str, model: str, attempts: int = 3) -> Any:
        """Call factory() → (result, usage) with retry on connection errors."""
        last_exc: Exception | None = None
        for attempt in range(attempts):
            if attempt:
                delay = 2.0 * attempt
                logger.warning(f"LM Studio retry {attempt}/{attempts - 1} ({task}) через {delay:.0f}с…")
                await asyncio.sleep(delay)
            t = time.perf_counter()
            try:
                result, usage = await factory()
                self._log(
                    model=model,
                    task=task,
                    prompt_tokens=getattr(usage, "prompt_tokens", 0),
                    completion_tokens=getattr(usage, "completion_tokens", 0),
                    duration_ms=(time.perf_counter() - t) * 1000,
                )
                return result
            except (APIConnectionError, APITimeoutError) as exc:
                last_exc = exc
                self._log(
                    model=model, task=task,
                    duration_ms=(time.perf_counter() - t) * 1000,
                    error=str(exc)[:200],
                )
            except BadRequestError as exc:
                # LM Studio иногда оборачивает флейк движка (peg-native/harmony-парсер
                # gpt-oss) в 400 — это ошибка сервера, а не запроса: ретраить.
                if "peg-native" not in str(exc):
                    raise
                last_exc = exc
                self._log(
                    model=model, task=task,
                    duration_ms=(time.perf_counter() - t) * 1000,
                    error=f"peg-native flake: {str(exc)[:150]}",
                )
        raise LMStudioError(
            f"LM Studio недоступен после {attempts} попыток ({task}).\n"
            f"Проверь: http://localhost:1234/v1/models\n"
            f"Ошибка: {last_exc}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: int | None = None,
        on_token: "callable | None" = None,
    ) -> str:
        """Send chat completion. Thinking mode disabled for Qwen3 models.
        on_token(str) — стрим: коллбек на каждый чанк (первый токен без ожидания полного ответа)."""
        t_out = timeout or self._cfg.lm_studio_timeout_fast

        async def _call():
            if on_token is None:
                resp = await self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=t_out,
                    extra_body={"enable_thinking": False},  # Qwen3: отключить thinking
                )
                msg = resp.choices[0].message
                # Fallback: thinking models иногда кладут ответ в reasoning_content
                content = msg.content or getattr(msg, "reasoning_content", "") or ""
                return content, resp.usage

            # стрим-путь: ретрай допустим только до первого токена (частичный ответ ретраить нельзя)
            parts: list[str] = []
            usage = None
            stream = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=t_out,
                stream=True,
                extra_body={"enable_thinking": False},
            )
            it = stream.__aiter__()
            while True:
                try:
                    chunk = await it.__anext__()
                except StopAsyncIteration:
                    break
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                for ch in chunk.choices or []:
                    if ch.delta and ch.delta.content:
                        parts.append(ch.delta.content)
                        on_token(ch.delta.content)
            content = "".join(parts)
            if not content and usage is None:
                # пустой стрим без usage = фейл до генерации: ретрай через проброс
                raise APIConnectionError(message="пустой стрим ответа", request=None)
            return content, usage

        return await self._retry(_call, task="chat", model=model)


    async def embed(self, texts: list[str], model: str = "text-embedding-bge-m3") -> list[list[float]]:
        """Get embeddings for a list of texts."""
        async def _call():
            # Явный таймаут: без него SDK-дефолт 600с < времени большого батча →
            # SDK молча перезапускает, а сервер до конца считает «трупы»
            resp = await self._client.embeddings.create(
                model=model, input=texts,
                timeout=self._cfg.lm_studio_timeout_reasoning,
            )
            return [item.embedding for item in resp.data], getattr(resp, "usage", None)

        return await self._retry(_call, task="embed", model=model)

    async def vision_chat(
        self,
        image_paths: list[Path],
        prompt: str,
        model: str,
    ) -> str:
        """Send vision request with base64-encoded images."""
        content: list[dict] = [{"type": "text", "text": prompt}]
        for p in image_paths:
            b64 = base64.b64encode(p.read_bytes()).decode()
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
            )

        async def _call():
            resp = await self._client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                temperature=0.3,
                max_tokens=2048,
                timeout=self._cfg.lm_studio_timeout_fast,
            )
            return resp.choices[0].message.content or "", resp.usage

        return await self._retry(_call, task="vision", model=model)

    async def list_loaded_models(self) -> list[str]:
        """Return IDs of models currently loaded in LM Studio."""
        try:
            resp = await self._client.models.list()
            return [m.id for m in resp.data]
        except (APIConnectionError, APITimeoutError) as exc:
            raise LMStudioError(
                f"LM Studio недоступен.\nПроверь: http://localhost:1234/v1/models\n{exc}"
            ) from exc
