"""T-M2-01: Structured JSON logging.

All log records are emitted as single-line JSON objects for ingestion
by Loki / ELK / CloudWatch without additional parsing.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime


class JSONFormatter(logging.Formatter):
    """Format a ``LogRecord`` as a single-line JSON string."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        # Carry over structured extras (e.g. request_id, method, path)
        # T-M4-01: AI gateway metadata is whitelisted here so redacted
        # usage/failure logs actually reach the JSON output. Message
        # content (user input / model output) must NEVER be logged.
        for key in (
            "request_id", "method", "path", "status_code", "duration_ms",
            "ai_task", "ai_model", "ai_provider", "prompt_tokens",
            "completion_tokens", "attempt", "outcome",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """Replace root + uvicorn log handlers with JSON formatters."""
    fmt = JSONFormatter()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "aozora"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.propagate = False
