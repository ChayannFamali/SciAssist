"""Loguru setup — call setup_logging() once at startup."""
import sys
from pathlib import Path

from loguru import logger

_configured = False


def setup_logging(debug: bool = False) -> None:
    """Configure loguru handlers. Safe to call multiple times."""
    global _configured
    if _configured:
        return
    _configured = True

    from sciassist.config import get_settings  # late import avoids circular

    log_dir: Path = get_settings().logs_path
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()

    # Console — INFO or DEBUG
    logger.add(
        sys.stderr,
        level="DEBUG" if debug else "INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        colorize=True,
    )

    # File — always DEBUG, rotating
    logger.add(
        str(log_dir / "sciassist.log"),
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} - {message}",
        rotation="10 MB",
        retention="30 days",
        encoding="utf-8",
        enqueue=True,  # thread-safe
    )
