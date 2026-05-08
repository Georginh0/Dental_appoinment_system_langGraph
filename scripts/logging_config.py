"""
scripts/logging_config.py — Centralised logging with sensitive-data redaction
==============================================================================
Import this ONCE at the top of any entrypoint (dental_agent.py, telegram_bot.py, etc.)
before any other imports that might trigger logging.

SECURITY: Redacts bot tokens, API keys, and passwords from all log records so
they never appear in dentai_agent.log or stdout.

USAGE:
    from scripts.logging_config import configure_logging
    configure_logging()            # call once at module load
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path


# ── Patterns to redact ────────────────────────────────────────
# Each tuple: (compiled_regex, replacement_string)
_REDACT_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Telegram bot token in URLs  e.g. /bot7123456789:AAFxxxxxx/
    (re.compile(r"(bot)\d{8,12}:[A-Za-z0-9_-]{35}", re.IGNORECASE), r"\1[REDACTED]"),

    # Generic API keys / bearer tokens in headers
    (re.compile(r"(Bearer\s+)[A-Za-z0-9\-_\.]{20,}", re.IGNORECASE), r"\1[REDACTED]"),

    # Groq / OpenAI keys  gsk_xxx  or sk-xxx
    (re.compile(r"\b(gsk_|sk-)[A-Za-z0-9]{20,}\b"), r"\1[REDACTED]"),

    # Database connection strings  postgresql://user:password@host
    (re.compile(r"(postgresql://[^:]+:)[^@]+(@)", re.IGNORECASE), r"\1[REDACTED]\2"),

    # Generic password= patterns in query strings
    (re.compile(r"(password=)[^\s&\"']+", re.IGNORECASE), r"\1[REDACTED]"),
]


class _RedactingFilter(logging.Filter):
    """Strip secrets from every log record before emission."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._redact(str(v)) for k, v in record.args.items()}
            else:
                record.args = tuple(self._redact(str(a)) for a in record.args)
        return True

    @staticmethod
    def _redact(text: str) -> str:
        for pattern, replacement in _REDACT_PATTERNS:
            text = pattern.sub(replacement, text)
        return text


def configure_logging(
    level: str = "INFO",
    log_file: str = "dentai_agent.log",
    max_bytes: int = 10 * 1024 * 1024,   # 10 MB
    backup_count: int = 5,
) -> None:
    """
    Configure root logger with:
    - Console handler (INFO level)
    - Rotating file handler (level from env LOG_LEVEL, default INFO)
    - Token-redacting filter on ALL handlers

    Call this before any other logging in your application.

    Args:
        level:        Default log level string ('DEBUG', 'INFO', 'WARNING', etc.)
        log_file:     Path to the rotating log file.
        max_bytes:    Maximum size per log file before rotation.
        backup_count: Number of rotated files to keep.
    """
    from logging.handlers import RotatingFileHandler

    effective_level = getattr(logging, os.environ.get("LOG_LEVEL", level).upper(), logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    redact_filter = _RedactingFilter()

    # ── Console handler ──────────────────────────────────────
    console = logging.StreamHandler()
    console.setLevel(effective_level)
    console.setFormatter(fmt)
    console.addFilter(redact_filter)

    # ── Rotating file handler ────────────────────────────────
    log_path = Path(log_file)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(effective_level)
    file_handler.setFormatter(fmt)
    file_handler.addFilter(redact_filter)

    # ── Root logger ──────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(effective_level)

    # Avoid duplicate handlers if configure_logging() is called multiple times
    if not root.handlers:
        root.addHandler(console)
        root.addHandler(file_handler)
    else:
        # Re-apply the redacting filter to any already-registered handlers
        for handler in root.handlers:
            if not any(isinstance(f, _RedactingFilter) for f in handler.filters):
                handler.addFilter(redact_filter)

    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.WARNING)
    logging.getLogger("groq").setLevel(logging.WARNING)
    logging.getLogger("langchain").setLevel(logging.WARNING)
    logging.getLogger("langgraph").setLevel(logging.WARNING)
