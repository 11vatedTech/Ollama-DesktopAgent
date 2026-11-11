"""Local-only structured logging for operational diagnostics.

Logs are JSON Lines and stay under the application data directory. Callers must
still avoid putting secrets in event names or metadata; the formatter applies
the same heuristic redaction used for model-visible output as a second line of
defence, not as a guarantee that arbitrary secrets can never be logged.
"""

from __future__ import annotations

import json
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

LOGGER_NAME = "ollama_workstation"
LOG_FILENAME = "workstation.jsonl"
_SENSITIVE_KEY = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?key|auth[_-]?token|oauth[_-]?token|"
    r"session[_-]?token|client[_-]?secret|password|passwd|secret|token|"
    r"private[_-]?key|refresh[_-]?token|database[_-]?url)"
)


def redact_text(value: Any) -> str:
    """Redact credential-like values using the workspace redaction policy."""
    text = str(value)
    try:
        from ollama_workstation.agents.tools import WorkspaceTools

        return WorkspaceTools.redact_sensitive_text(text)[0]
    except Exception:
        return text


def redact_data(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded, JSON-safe copy of event metadata with secrets redacted."""
    if depth > 5:
        return "[truncated]"
    if isinstance(value, str):
        return redact_text(value)[:4_000]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:40]:
            name = str(key)[:120]
            if _SENSITIVE_KEY.search(name) and item not in (None, ""):
                result[name] = "[REDACTED]"
            else:
                result[name] = redact_data(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [redact_data(item, depth=depth + 1) for item in list(value)[:40]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_text(value)[:1_000]


class JsonFormatter(logging.Formatter):
    """Emit one stable JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        standard = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "message", "asctime",
        }
        for key, value in record.__dict__.items():
            if key in standard or key.startswith("_"):
                continue
            payload[key] = redact_data(value)
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def configure_logging(logs_dir: Path, *, level: int = logging.INFO) -> Path:
    """Configure the application logger and return its JSONL path.

    Configuration is idempotent for the same directory, which is important for
    test processes and for a desktop window that is rebuilt in one process.
    """
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    destination = logs_dir / LOG_FILENAME
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        if getattr(handler, "_ollama_workstation_handler", False):
            if Path(getattr(handler, "baseFilename", "")).resolve() == destination.resolve():
                return destination
            logger.removeHandler(handler)
            handler.close()
    handler = RotatingFileHandler(
        destination, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(JsonFormatter())
    handler.setLevel(level)
    handler._ollama_workstation_handler = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    return destination


def log_event(event: str, message: str, *, level: int = logging.INFO, **fields: Any) -> None:
    """Write a structured event without requiring callers to know the handler."""
    logging.getLogger(LOGGER_NAME).log(
        level,
        redact_text(message),
        extra={"event": event, **fields},
    )


def log_path(logs_dir: Path) -> Path:
    return Path(logs_dir) / LOG_FILENAME
