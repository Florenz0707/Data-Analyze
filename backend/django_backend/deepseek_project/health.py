"""Dependency health checks with explicit liveness and readiness semantics."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from data_pipeline import IndexStateStore
from django.conf import settings
from django.core.cache import cache
from django.db import connection

from .metrics import metrics
from .model_runtime import cached_provider_health


def liveness_payload() -> dict[str, Any]:
    """Return a process-only check that never touches external dependencies."""
    return {"status": "ok", "check": "liveness"}


def readiness_payload() -> tuple[int, dict[str, Any]]:
    checks = {
        "configuration": _run_check(_configuration_check),
        "database": _run_check(_database_check),
        "cache": _run_check(_cache_check),
        "index": _run_check(_index_check),
    }
    ready = all(item["ok"] for item in checks.values())
    payload = {
        "status": "ready" if ready else "not_ready",
        "check": "readiness",
        "checks": checks,
    }
    return (200 if ready else 503), payload


def provider_health_payload() -> dict[str, Any]:
    configured: set[str] = set()
    configuration_error = None
    try:
        from .configuration import load_llm_config

        config = load_llm_config(validate_paths=False)
        configured.add(str(config.get("LLM_PROVIDER", "")).lower())
        configured.add(str(config.get("EMBEDDING_PROVIDER", "")).lower())
    except Exception as exc:
        configuration_error = type(exc).__name__
    configured.discard("")
    entries = cached_provider_health()
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_provider.setdefault(entry["provider"], []).append(entry)
        configured.add(entry["provider"])
    providers = []
    for provider in sorted(configured):
        instances = by_provider.get(provider, [])
        if any(item["status"] == "unhealthy" for item in instances):
            status = "unhealthy"
        elif instances:
            status = "healthy"
        elif not getattr(settings, "ENABLE_LLM", True):
            status = "disabled"
        else:
            status = "not_loaded"
        providers.append(
            {
                "provider": provider,
                "status": status,
                "loaded_instances": len(instances),
                "instances": instances,
            }
        )
    if configuration_error:
        return {
            "status": "error",
            "configuration_error": configuration_error,
            "providers": providers,
        }
    return {"status": "ok", "providers": providers}


def _run_check(checker: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        result = checker()
    except Exception as exc:
        result = {"ok": False, "reason": type(exc).__name__}
    metrics.increment(
        "deepseek_health_checks_total",
        labels={
            "check": str(result.get("check", "unknown")),
            "outcome": "ok" if result["ok"] else "error",
        },
    )
    return result


def _configuration_check() -> dict[str, Any]:
    if not str(getattr(settings, "SECRET_KEY", "")):
        return {"ok": False, "check": "configuration", "reason": "secret_key_missing"}
    if getattr(settings, "ENABLE_LLM", True):
        from .configuration import load_llm_config

        load_llm_config(validate_paths=False)
    return {
        "ok": True,
        "check": "configuration",
        "llm_enabled": bool(getattr(settings, "ENABLE_LLM", True)),
    }


def _database_check() -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return {"ok": True, "check": "database"}


def _cache_check() -> dict[str, Any]:
    key = f"deepseek:health:{uuid.uuid4().hex}"
    try:
        cache.set(key, "ok", timeout=5)
        if cache.get(key) != "ok":
            return {"ok": False, "check": "cache", "reason": "probe_mismatch"}
        return {"ok": True, "check": "cache"}
    finally:
        cache.delete(key)


def _index_check() -> dict[str, Any]:
    state_path = Path(
        getattr(
            settings,
            "INDEX_STATE_FILE",
            Path(settings.BASE_DIR) / "data" / "vector_stores" / ".index_state.json",
        )
    )
    state = IndexStateStore(state_path).load()
    current = state.get("current_version")
    entry = (state.get("versions") or {}).get(current) if current else None
    if not current or not isinstance(entry, dict) or entry.get("status") != "ready":
        return {"ok": False, "check": "index", "reason": "no_ready_current_index"}
    return {
        "ok": True,
        "check": "index",
        "version": current,
        "document_count": int(entry.get("document_count", 0)),
    }
