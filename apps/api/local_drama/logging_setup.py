"""Process-wide local file logging for the LOCAL_ONLY API and worker processes.

All application loggers live under the ``local_drama`` namespace and share one
single-line JSON format (compatible with ``middleware._log_request``). Each
process writes a role-and-PID file with size-based rotation, avoiding unsafe
cross-process rollover on Windows. WARNING+ is also mirrored to stderr.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_STREAM_HANDLER: logging.Handler | None = None
_FILE_HANDLERS: dict[str, RotatingFileHandler] = {}
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3

_TRUTHY_DISABLED = {"0", "false", "off", "no"}


class JsonLineFormatter(logging.Formatter):
    """Render every record as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        message = record.getMessage()
        try:
            parsed = json.loads(message)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            payload.update(parsed)
        else:
            payload["message"] = message
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def configure_logging(settings: Any = None, *, process_role: str = "service") -> None:
    """Configure one stream handler and one replaceable file handler per role."""
    global _STREAM_HANDLER
    level_name = os.environ.get("LOCAL_DRAMA_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    formatter = JsonLineFormatter()
    root = logging.getLogger("local_drama")
    root.setLevel(level)
    root.propagate = False
    if _STREAM_HANDLER is None:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setLevel(max(level, logging.WARNING))
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)
        _STREAM_HANDLER = stream_handler
    else:
        _STREAM_HANDLER.setLevel(max(level, logging.WARNING))
    file_disabled = os.environ.get("LOCAL_DRAMA_LOG_FILE", "").strip().casefold() in _TRUTHY_DISABLED
    logs_root = getattr(settings, "logs_root", None) if settings is not None else None
    existing = _FILE_HANDLERS.pop(process_role, None)
    if existing is not None:
        root.removeHandler(existing)
        existing.close()
    if not file_disabled and logs_root is not None:
        try:
            root_path = Path(logs_root)
            root_path.mkdir(parents=True, exist_ok=True)
            safe_role = "".join(
                character
                for character in process_role.casefold()
                if character.isalnum() or character in {"-", "_"}
            ) or "service"
            file_handler = RotatingFileHandler(
                root_path / f"local_drama-{safe_role}-{os.getpid()}.log",
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
                delay=True,
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
            _FILE_HANDLERS[process_role] = file_handler
        except OSError as error:
            root.warning(
                "logging.file_handler_failed error_type=%s error=%s",
                type(error).__name__,
                str(error)[:200],
            )


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger under the shared ``local_drama`` tree."""
    return logging.getLogger(f"local_drama.{name}")
