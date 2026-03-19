"""Structured JSON logging configuration."""

import json
import logging
import sys
import traceback
from datetime import UTC, datetime


class StructuredFormatter(logging.Formatter):
    """Emit each log record as a single JSON line (Cloud Logging compatible)."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        # Attach extra fields if present
        for key in ("trace_id", "span_id", "ticker", "user_query", "latency_ms", "status_code"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val

        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = traceback.format_exception(*record.exc_info)

        return json.dumps(log_entry, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with structured JSON output."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()
    root.addHandler(handler)

    # Silence noisy libraries
    for name in ("uvicorn.access", "uvicorn.error", "httpcore", "httpx"):
        logging.getLogger(name).setLevel(logging.WARNING)
