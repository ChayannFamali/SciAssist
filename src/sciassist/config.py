"""Application settings via pydantic-settings + YAML."""
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Zotero — local only, NO api.zotero.org
    zotero_data_dir: Path = Path("D:/libraries")
    zotero_storage_dir: Path = Path("D:/libraries/storage")
    zotero_local_url: str = "http://127.0.0.1:23119"

    # Obsidian
    obsidian_vault: Path = Path("D:/SciVault")
    obsidian_papers_folder: str = "papers"
    obsidian_concepts_folder: str = "concepts"
    obsidian_datasets_folder: str = "datasets"

    # LM Studio
    lm_studio_url: str = "http://localhost:1234/v1"
    lm_studio_timeout_reasoning: int = 300
    lm_studio_timeout_fast: int = 60

    # Data paths
    project_root: Path = Path("H:/SciAssist")
    chroma_db_path: Path = Path("H:/SciAssist/data/chroma_db")
    logs_path: Path = Path("H:/SciAssist/data/logs")
    raw_markdown_path: Path = Path("H:/SciAssist/data/raw_markdown")
    extracted_figures_path: Path = Path("H:/SciAssist/data/extracted_figures")
    registry_path: Path = Path("H:/SciAssist/data/processed_registry.json")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached singleton settings."""
    return Settings()


@lru_cache(maxsize=1)
def get_yaml_config() -> dict:
    """Load configs/settings.yaml (non-secret runtime config)."""
    path = get_settings().project_root / "configs" / "settings.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
