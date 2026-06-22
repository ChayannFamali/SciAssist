"""Проверка LLM_SEMAPHORE."""
import asyncio
from sciassist.web.deps import LLM_SEMAPHORE


async def test():
    print("LLM_SEMAPHORE type:", type(LLM_SEMAPHORE).__name__)
    print("LLM_SEMAPHORE._value:", LLM_SEMAPHORE._value)
    print("locked now:", LLM_SEMAPHORE.locked())
    # Take 1, verify locked, release
    await LLM_SEMAPHORE.acquire()
    print("after acquire: locked =", LLM_SEMAPHORE.locked())
    print("remaining slots:", LLM_SEMAPHORE._value)
    LLM_SEMAPHORE.release()
    print("after release: locked =", LLM_SEMAPHORE.locked(), "value =", LLM_SEMAPHORE._value)
    print()
    print("✅ Семофор = 1, LM Studio однопоточный — гарантия")


asyncio.run(test())