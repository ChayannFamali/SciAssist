"""Model selection based on task type."""
from dataclasses import dataclass
from pathlib import Path

import yaml

from sciassist.config import get_settings


@dataclass
class ModelSpec:
    name: str
    max_context: int
    temperature: float
    timeout: int


class ModelRouter:
    """Reads model_router.yaml and selects the best model for a task."""

    def __init__(self) -> None:
        path = get_settings().project_root / "configs" / "model_router.yaml"
        with open(path, encoding="utf-8") as f:
            self._rules: dict = yaml.safe_load(f).get("rules", {})

    def select(self, task: str, context_size: int = 0) -> ModelSpec:
        """Return ModelSpec for the given task. Falls back if context too large."""
        rule = self._rules.get(task)
        if rule is None:
            available = list(self._rules)
            raise ValueError(f"Unknown task '{task}'. Available: {available}")

        max_ctx = rule.get("max_context", 8000)
        temp = rule.get("temperature", 0.3)
        timeout = rule.get("timeout", 120)
        primary = rule["primary"]

        # Use fallback if context_size exceeds primary limit
        if context_size > max_ctx:
            for fb in rule.get("fallback", []):
                return ModelSpec(name=fb, max_context=max_ctx, temperature=temp, timeout=timeout)

        return ModelSpec(name=primary, max_context=max_ctx, temperature=temp, timeout=timeout)

    def embed_model(self) -> str:
        return self._rules.get("embed", {}).get("primary", "text-embedding-bge-m3")
