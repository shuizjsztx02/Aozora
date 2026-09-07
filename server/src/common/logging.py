"""T-M2-01: Structured JSON logging.

All log records are emitted as single-line JSON objects for ingestion
by Loki / ELK / CloudWatch without additional parsing.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime

# T-M5-02d: pattern matching sensitive field names that must be redacted
# before being written to logs.  Two-branch approach:
#   • Matches both underscore forms (``api_key``, ``auth_token``) and
#     hyphen forms (``x-api-key``, ``x-refresh-token``).
#   • Uses lookbehind ``(?<=[^a-z0-9])`` instead of ``\b`` so that
#     prefixed attrs like ``x_api_key`` and ``auth_token`` are caught.
#   • Negative: ``prompt_tokens`` / ``completion_tokens`` (plural ``tokens``
#     is not matched by singular ``token`` alternative).
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(?:^|(?<=[^a-z0-9]))"
    r"(token|refresh[_-]token|password|secret|api[_-]key|apikey|secret[_-]key|authorization)"
    r"(?=\b|[_-])",
)
_REDACTED = "***"


class SensitiveFieldFilter(logging.Filter):
    """Redact sensitive fields from ``LogRecord.extra`` before formatting.

    Any attribute whose key matches ``_SENSITIVE_KEY_RE`` is replaced
    with ``***`` in the JSON output.  This provides defence-in-depth
    alongside the existing extras whitelist (which already prevents
    arbitrary keys from reaching the output).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        for attr in list(vars(record)):
            if _SENSITIVE_KEY_RE.search(attr):
                setattr(record, attr, _REDACTED)
        return True


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
                # T-M5-02d: redact sensitive field values in the output.
                if _SENSITIVE_KEY_RE.search(key):
                    value = _REDACTED
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """Replace root + uvicorn log handlers with JSON formatters."""
    fmt = JSONFormatter()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)
    # T-M5-02d: defence-in-depth redaction filter on the handler.
    handler.addFilter(SensitiveFieldFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "aozora"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.propagate = False
