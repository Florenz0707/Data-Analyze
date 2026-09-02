"""Shared cache primitives, bounded values, single-flight and diagnostics."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_metrics = Counter()
_metrics_lock = threading.Lock()
_inflight: dict[str, Future[Any]] = {}
_inflight_lock = threading.Lock()


def _increment(metric: str, amount: int = 1) -> None:
    with _metrics_lock:
        _metrics[metric] += amount


def cache_value_size(value: Any) -> int:
    """Return the UTF-8 size of the value's cache representation."""
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        encoded = repr(value)
    return len(encoded.encode("utf-8"))


def cache_value_allowed(value: Any, *, max_bytes: int | None = None) -> bool:
    """Reject values that could evict useful entries from the shared cache."""
    limit = max_bytes or int(getattr(settings, "CACHE_MAX_OBJECT_BYTES", 262_144))
    allowed = cache_value_size(value) <= limit
    if not allowed:
        _increment("skipped_large")
    return allowed


def cache_get(key: str, *, cache_kind: str) -> Any:
    """Read a cache value and degrade to a miss when Redis is unavailable."""
    try:
        value = cache.get(key)
    except Exception:
        _increment("errors")
        logger.warning("缓存读取失败：kind=%s", cache_kind, exc_info=True)
        return None
    if value is None:
        _increment(f"{cache_kind}_misses")
    else:
        _increment(f"{cache_kind}_hits")
    return value


def cache_set(
    key: str,
    value: Any,
    timeout: int,
    *,
    cache_kind: str,
    max_bytes: int | None = None,
) -> bool:
    """Write a bounded cache value without making the request depend on Redis."""
    if timeout <= 0 or not cache_value_allowed(value, max_bytes=max_bytes):
        return False
    try:
        cache.set(key, value, timeout)
    except Exception:
        _increment("errors")
        logger.warning("缓存写入失败：kind=%s", cache_kind, exc_info=True)
        return False
    _increment(f"{cache_kind}_writes")
    return True


def cache_delete(key: str, *, cache_kind: str) -> None:
    try:
        cache.delete(key)
    except Exception:
        _increment("errors")
        logger.warning("缓存删除失败：kind=%s", cache_kind, exc_info=True)


def _distributed_lock_key(cache_key: str) -> str:
    return f"deepseek:cache-lock:{cache_key}"


def _acquire_distributed_lock(cache_key: str) -> tuple[bool, str]:
    """Acquire an atomic Redis/Django cache lock, or wait for its result."""
    token = uuid.uuid4().hex
    timeout = int(getattr(settings, "CACHE_SINGLE_FLIGHT_TIMEOUT", 120))
    lock_key = _distributed_lock_key(cache_key)
    try:
        if cache.add(lock_key, token, timeout):
            return True, lock_key
    except Exception:
        _increment("errors")
        logger.warning("缓存锁获取失败，退回进程内合并：key=%s", cache_key, exc_info=True)
        return True, ""

    _increment("stampede_waits")
    deadline = time.monotonic() + min(timeout, 30)
    while time.monotonic() < deadline:
        if cache_get(cache_key, cache_kind="lock_wait") is not None:
            return False, ""
        time.sleep(0.05)
    # The original owner may have failed or the value may intentionally be too
    # large to cache. Computing after a bounded wait preserves availability.
    return True, ""


def _release_distributed_lock(lock_key: str) -> None:
    if not lock_key:
        return
    try:
        cache.delete(lock_key)
    except Exception:
        _increment("errors")
        logger.warning("缓存锁释放失败：key=%s", lock_key, exc_info=True)


def get_or_compute(
    key: str,
    producer: Callable[[], Any],
    *,
    timeout: int,
    cache_kind: str,
    validator: Callable[[Any], bool],
    max_bytes: int | None = None,
) -> tuple[Any, bool]:
    """Return a cached value or merge concurrent computations for one key.

    The local Future avoids duplicate work inside one worker. Redis ``add``
    provides the cross-worker lock when the configured shared backend supports
    it. A failed cache never turns a model or retrieval request into an error.
    """
    cached = cache_get(key, cache_kind=cache_kind)
    if validator(cached):
        return cached, True
    if cached is not None:
        cache_delete(key, cache_kind=cache_kind)

    with _inflight_lock:
        future = _inflight.get(key)
        if future is None:
            future = Future()
            _inflight[key] = future
            owner = True
        else:
            owner = False
            _increment("local_inflight_waits")

    if not owner:
        try:
            return future.result(
                timeout=int(getattr(settings, "CACHE_SINGLE_FLIGHT_TIMEOUT", 120))
            ), False
        except FutureTimeoutError:
            _increment("errors")
            raise TimeoutError(f"缓存单 Key 请求合并超时：{cache_kind}") from None

    lock_key = ""
    try:
        lock_owner, lock_key = _acquire_distributed_lock(key)
        if not lock_owner:
            value = cache_get(key, cache_kind=cache_kind)
            if validator(value):
                future.set_result(value)
                return value, True
        value = producer()
        if validator(value):
            cache_set(key, value, timeout, cache_kind=cache_kind, max_bytes=max_bytes)
        future.set_result(value)
        return value, False
    except BaseException as exc:
        future.set_exception(exc)
        raise
    finally:
        _release_distributed_lock(lock_key)
        with _inflight_lock:
            _inflight.pop(key, None)


def cache_metrics_snapshot() -> dict[str, Any]:
    """Return process counters plus Redis memory and eviction gauges."""
    with _metrics_lock:
        snapshot: dict[str, Any] = dict(_metrics)
    for key in (
        "reply_hits",
        "reply_misses",
        "retrieval_hits",
        "retrieval_misses",
        "reply_writes",
        "retrieval_writes",
        "skipped_large",
        "stampede_waits",
        "local_inflight_waits",
        "errors",
    ):
        snapshot.setdefault(key, 0)
    snapshot["hit_rate"] = _hit_rate(snapshot)

    backend = getattr(cache, "_cache", None)
    get_client = getattr(backend, "get_client", None)
    if callable(get_client):
        try:
            info = get_client(write=False).info()
            snapshot["redis_used_memory_bytes"] = int(info.get("used_memory", 0))
            snapshot["redis_evicted_keys"] = int(info.get("evicted_keys", 0))
        except Exception:
            _increment("errors")
            logger.warning("读取 Redis memory/stats 失败", exc_info=True)
    return snapshot


def _hit_rate(snapshot: dict[str, Any]) -> float:
    hits = int(snapshot.get("reply_hits", 0)) + int(snapshot.get("retrieval_hits", 0))
    misses = int(snapshot.get("reply_misses", 0)) + int(snapshot.get("retrieval_misses", 0))
    return hits / (hits + misses) if hits + misses else 0.0


def reset_cache_metrics() -> None:
    """Reset process counters for deterministic tests and local diagnostics."""
    with _metrics_lock:
        _metrics.clear()


def build_retrieval_cache_key(
    query: str,
    *,
    index_version: str,
    embedding_provider: str,
    embedding_model: str,
    top_k: int,
    retrieval_mode: str,
    candidate_multiplier: int,
    min_score: float,
    vector_weight: float,
    lexical_weight: float,
    reranker_enabled: bool,
    metadata_filter: dict[str, Any],
    schema_version: str,
    namespace: str,
) -> str:
    """Build a retrieval-only key; no user prompt is stored in Redis keys."""
    identity = {
        "query": query,
        "index_version": index_version,
        "embedding_provider": embedding_provider,
        "embedding_model": embedding_model,
        "top_k": top_k,
        "retrieval_mode": retrieval_mode,
        "candidate_multiplier": candidate_multiplier,
        "min_score": min_score,
        "vector_weight": vector_weight,
        "lexical_weight": lexical_weight,
        "reranker_enabled": reranker_enabled,
        "metadata_filter": metadata_filter,
        "schema_version": schema_version,
        "namespace": namespace,
    }
    text = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return f"retrieval:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
