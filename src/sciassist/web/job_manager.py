"""Менеджер фоновых задач в памяти.

Один воркер: новые джобы встают в очередь. Все LLM-вызовы уже под
общим LLM_SEMAPHORE в services.py (через QueryEngine / LMStudioClient),
поэтому конкурентных LLM-запросов не будет даже без этого менеджера.
Менеджер нужен для прогресса в UI и сериализации длинных операций.

Статусы: queued → running → done | error | cancelled
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Optional

JobFn = Callable[..., Awaitable[dict]]


@dataclass
class Job:
    id: str
    kind: str                                # "process" | "gaps" | "draft" | "analyze"
    args: dict
    status: str = "queued"                   # queued | running | done | error | cancelled
    step: str = ""                           # последний человекочитаемый шаг
    progress: float = 0.0                    # 0..1
    result: Optional[dict] = None            # {"ok": bool, "data"|"error": ...}
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # не светить внутренности в API
        return d


class JobManager:
    """Простой однопоточный менеджер джоб в памяти.

    • Нет персистентности — при рестарте сервера джобы теряются.
    • Один воркер — concurrent джобы последовательны.
    • Можно получить статус по id и отменить.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

    def start(self) -> None:
        """Запустить воркер. Идемпотентно."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        """Остановить воркер, отменить все джобы."""
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        for t in self._tasks.values():
            t.cancel()
        self._tasks.clear()

    async def submit(self, kind: str, fn: JobFn, **args) -> str:
        """Поставить джобу в очередь. Возвращает job_id.

        Должен вызываться из event loop (не из threadpool).
        """
        job_id = uuid.uuid4().hex[:12]
        job = Job(id=job_id, kind=kind, args=args)
        self._jobs[job_id] = job

        async def _runner() -> None:
            job.status = "running"
            job.started_at = time.time()
            try:
                async def on_step(msg: str) -> None:
                    job.step = msg
                result = await fn(on_step=on_step, **args)
                job.result = result
                job.ok = bool(result.get("ok")) if isinstance(result, dict) else None
                job.status = "done" if result.get("ok") else "error"
                job.error = result.get("error") if isinstance(result, dict) and not result.get("ok") else None
                job.progress = 1.0
            except asyncio.CancelledError:
                job.status = "cancelled"
                raise
            except Exception as e:
                job.status = "error"
                job.error = str(e)
            finally:
                job.finished_at = time.time()

        task = asyncio.create_task(_runner())
        self._tasks[job_id] = task
        await self._queue.put(job_id)
        return job_id

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self, *, limit: int = 50) -> list[Job]:
        # новые сверху
        jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def cancel(self, job_id: str) -> bool:
        """Отменить джобу. True если нашли и отменили."""
        job = self._jobs.get(job_id)
        if job is None:
            return False
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            job.status = "cancelled"
            return True
        return False

    async def _worker(self) -> None:
        """Вытаскивает job_id из очереди и ждёт завершения соответствующего таска."""
        while True:
            job_id = await self._queue.get()
            task = self._tasks.get(job_id)
            if task is None:
                continue
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                # ошибка уже записана в _runner
                pass
            finally:
                self._tasks.pop(job_id, None)


# Синглтон менеджера — один на процесс
_manager: JobManager | None = None


def get_job_manager() -> JobManager:
    global _manager
    if _manager is None:
        _manager = JobManager()
        _manager.start()
    return _manager


__all__ = ["Job", "JobManager", "get_job_manager"]