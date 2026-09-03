"""Request context and safe structured logging helpers."""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Mapping
from typing import Any

from .metrics import record_phase

_request_id = contextvars.ContextVar("request_id", default="-")
_trace_id = contextvars.ContextVar("trace_id", default="-")

_TRACE_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_REQUEST_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_SECRET_RE = re.compile(
    r"(?i)\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|secret)\b\s*[:=]\s*[^\s,;]+"
)
_STANDARD_LOG_RECORD_FIELDS = set(logging.LogRecord(None, 0, "", 0, "", (), None).__dict__)
_SENSITIVE_FIELD_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization|cookie)"
)


def _valid_request_id(value: str | None) -> str:
    if value and _REQUEST_ID_RE.fullmatch(value):
        return value.lower()
    return uuid.uuid4().hex


def _valid_trace_id(value: str | None) -> str:
    if value and _TRACE_ID_RE.fullmatch(value) and int(value, 16) != 0:
        return value.lower()
    return uuid.uuid4().hex


def resolve_request_context(headers: Mapping[str, Any]) -> tuple[str, str]:
    """Return safe request/trace IDs, accepting only UUID-shaped values."""
    request_id = headers.get("X-Request-ID") or headers.get("x-request-id")
    trace_id = headers.get("X-Trace-ID") or headers.get("x-trace-id")
    if not trace_id:
        traceparent = headers.get("traceparent")
        if isinstance(traceparent, str):
            parts = traceparent.split("-")
            trace_id = parts[1] if len(parts) == 4 else None
    return _valid_request_id(request_id), _valid_trace_id(trace_id)


def set_request_context(
    request_id: str, trace_id: str
) -> tuple[contextvars.Token, contextvars.Token]:
    """Set context variables and return tokens for restoring the previous context."""
    return _request_id.set(request_id), _trace_id.set(trace_id)


def reset_request_context(tokens: tuple[contextvars.Token, contextvars.Token]) -> None:
    _request_id.reset(tokens[0])
    _trace_id.reset(tokens[1])


def current_request_context() -> dict[str, str]:
    return {"request_id": _request_id.get(), "trace_id": _trace_id.get()}


def hash_identifier(value: str | None) -> str | None:
    """Return a short stable digest for a user/session identifier."""
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def hash_text(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def estimate_tokens(value: str | None) -> int:
    """Give a conservative, provider-independent token estimate for telemetry."""
    if not value:
        return 0
    return max(1, (len(value.encode("utf-8")) + 3) // 4)


def log_phase(logger: logging.Logger, phase: str, started: float, **fields: Any) -> None:
    """Emit one bounded phase timing event without accepting prompt contents."""
    duration_seconds = time.perf_counter() - started
    logger.info(
        "phase.completed",
        extra={
            "event": "phase.completed",
            "phase": phase,
            "duration_ms": round(duration_seconds * 1000, 3),
            **fields,
        },
    )
    record_phase(phase, duration_seconds, fields)


def _sanitize_text(value: str) -> str:
    value = _BEARER_RE.sub("Bearer [REDACTED]", value)

    def redact_secret(match: re.Match[str]) -> str:
        matched = match.group(0)
        separator = ":" if ":" in matched else "="
        return f"{matched.split(separator, 1)[0]}{separator} [REDACTED]"

    value = _SECRET_RE.sub(redact_secret, value)
    return value[:500]


def _safe_value(field: str, value: Any, depth: int = 0) -> Any:
    if _SENSITIVE_FIELD_RE.search(field):
        return "[REDACTED]"
    if field in {"request", "wsgi_request"}:
        return "[OMITTED]"
    if depth > 2:
        return "[TRUNCATED]"
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): _safe_value(str(key), item, depth + 1)
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(field, item, depth + 1) for item in list(value)[:100]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _sanitize_text(str(value))


class RequestContextFilter(logging.Filter):
    """Add the current request and trace IDs to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        context = current_request_context()
        record.request_id = context["request_id"]
        record.trace_id = context["trace_id"]
        return True


class LevelRangeFilter(logging.Filter):
    """Keep a handler within an inclusive logging level range."""

    def __init__(self, min_level: int | str = logging.NOTSET, max_level: int | str | None = None):
        super().__init__()
        self.min_level = self._coerce_level(min_level)
        self.max_level = self._coerce_level(max_level) if max_level is not None else None

    @staticmethod
    def _coerce_level(level: int | str) -> int:
        if isinstance(level, int):
            return level
        normalized = str(level).upper()
        resolved = logging.getLevelNamesMapping().get(normalized)
        if resolved is None:
            raise ValueError(f"unknown logging level: {level}")
        return resolved

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= self.min_level and (
            self.max_level is None or record.levelno <= self.max_level
        )


class JsonFormatter(logging.Formatter):
    """Serialize logs with a stable schema and redact credential-like fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": _sanitize_text(record.getMessage()),
            "request_id": getattr(record, "request_id", _request_id.get()),
            "trace_id": getattr(record, "trace_id", _trace_id.get()),
        }
        for field, value in record.__dict__.items():
            if field in _STANDARD_LOG_RECORD_FIELDS or field in payload:
                continue
            payload[field] = _safe_value(field, value)
        if record.exc_info:
            exception = record.exc_info[1]
            payload["exception"] = {
                "type": type(exception).__name__,
                "message": _sanitize_text(str(exception)),
            }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
