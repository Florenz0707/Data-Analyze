from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """Stable machine-readable codes shared by API responses and the frontend."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_INVALID = "AUTH_INVALID"
    AUTH_FORBIDDEN = "AUTH_FORBIDDEN"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


def error_payload(
    code: ErrorCode, message: str, *, details: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Build the backward-compatible, machine-readable error body."""
    payload: dict[str, Any] = {"code": code.value, "error": message}
    if details:
        payload["details"] = details
    return payload
