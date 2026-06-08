"""Custom exceptions for SciAssist."""


class SciAssistError(Exception):
    """Base exception."""


class ZoteroBackendError(SciAssistError):
    """Zotero unavailable via all backends."""


class LMStudioError(SciAssistError):
    """LM Studio unavailable or returned an error."""


class PDFProcessingError(SciAssistError):
    """PDF cannot be processed."""


class ConfigError(SciAssistError):
    """Invalid configuration."""
