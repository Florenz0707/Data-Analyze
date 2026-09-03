"""Request-safe model instance caching for provider runtimes."""

from __future__ import annotations

import resource
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from time import perf_counter
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class ModelInstanceKey:
    """Stable identity for one provider/model/endpoint combination."""

    provider: str
    model: str
    endpoint: str


@dataclass(frozen=True)
class ProviderClientKey:
    """Stable identity for one reusable remote provider HTTP client."""

    provider: str
    endpoint: str


@dataclass(frozen=True)
class ModelLoadRecord:
    """Observable metadata captured when a model or embedding is constructed."""

    key: ModelInstanceKey
    duration_seconds: float
    peak_rss_kib: int
    loaded_at: str


class HealthAwareModel:
    """Delegate a provider object while tracking failures for cache eviction."""

    def __init__(self, instance: Any, *, close_underlying: bool) -> None:
        self._instance = instance
        self._close_underlying = close_underlying
        self._healthy = True
        self._lock = RLock()

    def health_check(self) -> bool:
        """Return provider health without forcing a network request by default."""
        with self._lock:
            if not self._healthy:
                return False
        checker = getattr(self._instance, "health_check", None)
        if callable(checker):
            try:
                result = checker()
            except Exception:
                self.mark_unhealthy()
                return False
            if result is False:
                self.mark_unhealthy()
                return False
        return True

    def mark_unhealthy(self) -> None:
        with self._lock:
            self._healthy = False

    def close(self) -> None:
        if not self._close_underlying:
            return
        close = getattr(self._instance, "close", None)
        if callable(close):
            close()

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._instance, name)
        if not callable(value):
            return value

        def call(*args: Any, **kwargs: Any) -> Any:
            try:
                result = value(*args, **kwargs)
            except Exception:
                self.mark_unhealthy()
                raise
            if name == "stream" and hasattr(result, "__iter__"):
                return self._monitor_stream(result)
            return result

        return call

    def _monitor_stream(self, stream: Any) -> Any:
        try:
            yield from stream
        except Exception:
            self.mark_unhealthy()
            raise


class ModelInstanceCache[T]:
    """Thread-safe bounded cache that serializes first-time model construction."""

    def __init__(self, max_size: int = 4) -> None:
        if max_size < 1:
            raise ValueError("模型实例缓存容量必须是正整数")
        self.max_size = max_size
        self._items: OrderedDict[ModelInstanceKey, T] = OrderedDict()
        self._lock = RLock()

    def get_or_create(self, key: ModelInstanceKey, factory: Callable[[], T]) -> T:
        """Return the cached instance or construct and cache it exactly once per key."""
        with self._lock:
            cached = self._items.get(key)
            if cached is not None:
                health_check = getattr(cached, "health_check", None)
                if not callable(health_check) or health_check():
                    self._items.move_to_end(key)
                    return cached
                self._items.pop(key, None)
                self._close(cached)
            instance = factory()
            self._items[key] = instance
            self._items.move_to_end(key)
            while len(self._items) > self.max_size:
                _, evicted = self._items.popitem(last=False)
                self._close(evicted)
            return instance

    @staticmethod
    def _close(instance: Any) -> None:
        close = getattr(instance, "close", None)
        if callable(close):
            close()

    def clear(self) -> None:
        """Remove all cached instances, primarily for tests and controlled reloads."""
        with self._lock:
            for instance in self._items.values():
                self._close(instance)
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


_LLM_CACHE: ModelInstanceCache[Any] | None = None
_EMBEDDING_CACHE: ModelInstanceCache[Any] | None = None
_CLIENT_CACHE: ProviderClientCache[Any] | None = None
_MODEL_LOAD_RECORDS: dict[ModelInstanceKey, ModelLoadRecord] = {}
_CACHE_LOCK = RLock()


class ProviderClientCache[T]:
    """Bounded thread-safe cache for reusable remote HTTP clients."""

    def __init__(self, max_size: int = 4) -> None:
        if max_size < 1:
            raise ValueError("Provider 客户端缓存容量必须是正整数")
        self.max_size = max_size
        self._items: OrderedDict[ProviderClientKey, T] = OrderedDict()
        self._lock = RLock()

    def get_or_create(self, key: ProviderClientKey, factory: Callable[[], T]) -> T:
        with self._lock:
            cached = self._items.get(key)
            if cached is not None:
                self._items.move_to_end(key)
                return cached
            client = factory()
            self._items[key] = client
            self._items.move_to_end(key)
            while len(self._items) > self.max_size:
                _, evicted = self._items.popitem(last=False)
                self._close(evicted)
            return client

    @staticmethod
    def _close(client: Any) -> None:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    def clear(self) -> None:
        with self._lock:
            for client in self._items.values():
                self._close(client)
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


def _cache_size(config: dict[str, Any]) -> int:
    try:
        size = int(config.get("MODEL_CACHE_MAX_SIZE", 4))
    except (TypeError, ValueError) as exc:
        raise ValueError("MODEL_CACHE_MAX_SIZE 必须是正整数") from exc
    if size < 1:
        raise ValueError("MODEL_CACHE_MAX_SIZE 必须是正整数")
    return size


def _get_cache(kind: str, config: dict[str, Any]) -> ModelInstanceCache[Any]:
    global _LLM_CACHE, _EMBEDDING_CACHE
    with _CACHE_LOCK:
        cache = _LLM_CACHE if kind == "llm" else _EMBEDDING_CACHE
        if cache is None or cache.max_size != _cache_size(config):
            cache = ModelInstanceCache(_cache_size(config))
            if kind == "llm":
                _LLM_CACHE = cache
            else:
                _EMBEDDING_CACHE = cache
        return cache


def _get_client_cache(config: dict[str, Any]) -> ProviderClientCache[Any]:
    global _CLIENT_CACHE
    with _CACHE_LOCK:
        size = _cache_size(config)
        if _CLIENT_CACHE is None or _CLIENT_CACHE.max_size != size:
            if _CLIENT_CACHE is not None:
                _CLIENT_CACHE.clear()
            _CLIENT_CACHE = ProviderClientCache(size)
        return _CLIENT_CACHE


def get_cached_http_client(provider: str, endpoint: str, config: dict[str, Any]) -> Any:
    """Return one shared HTTP client per provider endpoint identity."""
    normalized_provider = provider.lower()
    key = ProviderClientKey(normalized_provider, endpoint)
    from deepseek_project.external_endpoint import create_safe_http_client

    return _get_client_cache(config).get_or_create(key, lambda: create_safe_http_client(endpoint))


def _peak_rss_kib() -> int:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(usage if usage > 0 else 0)


def model_load_records() -> tuple[ModelLoadRecord, ...]:
    """Return immutable load timing and memory records for diagnostics."""
    with _CACHE_LOCK:
        return tuple(_MODEL_LOAD_RECORDS.values())


def cached_provider_health() -> list[dict[str, Any]]:
    """Return health for loaded provider instances without making network calls."""
    result: list[dict[str, Any]] = []
    with _CACHE_LOCK:
        caches = (("llm", _LLM_CACHE), ("embedding", _EMBEDDING_CACHE))
        for kind, cache in caches:
            if cache is None:
                continue
            with cache._lock:
                items = list(cache._items.items())
            for key, instance in items:
                checker = getattr(instance, "health_check", None)
                healthy = True
                if callable(checker):
                    try:
                        healthy = bool(checker())
                    except Exception:
                        healthy = False
                result.append(
                    {
                        "kind": kind,
                        "provider": key.provider,
                        "model": key.model,
                        "status": "healthy" if healthy else "unhealthy",
                    }
                )
    return result


def model_runtime_snapshot() -> dict[str, int | float]:
    """Return bounded model cache and load diagnostics for metrics scraping."""
    with _CACHE_LOCK:
        records = tuple(_MODEL_LOAD_RECORDS.values())
        return {
            "model_cache_llm_entries": len(_LLM_CACHE) if _LLM_CACHE is not None else 0,
            "model_cache_embedding_entries": (
                len(_EMBEDDING_CACHE) if _EMBEDDING_CACHE is not None else 0
            ),
            "provider_client_cache_entries": len(_CLIENT_CACHE) if _CLIENT_CACHE is not None else 0,
            "model_load_records_total": len(records),
            "model_last_load_duration_seconds": max(
                (record.duration_seconds for record in records), default=0.0
            ),
            "model_peak_rss_bytes": max((record.peak_rss_kib for record in records), default=0)
            * 1024,
        }


def _build_managed_model(key: ModelInstanceKey, factory: Callable[[], Any]) -> HealthAwareModel:
    started = perf_counter()
    instance = HealthAwareModel(
        factory(), close_underlying=key.provider in {"transformers", "ollama"}
    )
    record = ModelLoadRecord(
        key=key,
        duration_seconds=perf_counter() - started,
        peak_rss_kib=_peak_rss_kib(),
        loaded_at=datetime.now(UTC).isoformat(),
    )
    with _CACHE_LOCK:
        _MODEL_LOAD_RECORDS[key] = record
    return instance


def configured_model(config: dict[str, Any], provider: str, *, embedding: bool = False) -> str:
    """Resolve the configured LLM or embedding model name for a provider."""
    sections = {
        "transformers": ("TRANSFORMERS_CONFIG", "embedding_model" if embedding else "llm_model"),
        "ollama": ("OLLAMA_CONFIG", "embedding_model" if embedding else "model"),
        "openai_compat": (
            "OPENAI_COMPAT_CONFIG",
            "embedding_model" if embedding else "model",
        ),
        "dashscope": (
            "DASHSCOPE_CONFIG",
            "embedding_model" if embedding else "chat_model",
        ),
    }
    section_name, model_key = sections[provider]
    value = (config.get(section_name) or {}).get(model_key)
    return str(value or "")


def configured_endpoint(config: dict[str, Any], provider: str) -> str:
    """Return the non-secret endpoint identity used in the model cache key."""
    section_name = {
        "transformers": "TRANSFORMERS_CONFIG",
        "ollama": "OLLAMA_CONFIG",
        "openai_compat": "OPENAI_COMPAT_CONFIG",
        "dashscope": "DASHSCOPE_CONFIG",
    }[provider]
    section = config.get(section_name) or {}
    return str(
        section.get("cache_identity") or section.get("base_url") or section.get("host") or ""
    )


def get_cached_llm(
    provider: str, model: str | None, config: dict[str, Any]
) -> tuple[Any, ModelInstanceKey]:
    """Return a bounded, shared LLM instance and its cache identity."""
    normalized_provider = provider.lower()
    resolved_model = (model or configured_model(config, normalized_provider)).strip()
    key = ModelInstanceKey(
        normalized_provider, resolved_model, configured_endpoint(config, normalized_provider)
    )
    cache = _get_cache("llm", config)
    from llm_provider_factory import build_llm_by

    return cache.get_or_create(
        key,
        lambda: _build_managed_model(
            key, lambda: build_llm_by(normalized_provider, config, model=resolved_model)
        ),
    ), key


def get_cached_embedding(
    provider: str, model: str | None, config: dict[str, Any]
) -> tuple[Any, ModelInstanceKey]:
    """Return a bounded, shared embedding instance and its cache identity."""
    normalized_provider = provider.lower()
    resolved_model = (
        model or configured_model(config, normalized_provider, embedding=True)
    ).strip()
    key = ModelInstanceKey(
        normalized_provider, resolved_model, configured_endpoint(config, normalized_provider)
    )
    cache = _get_cache("embedding", config)
    from llm_provider_factory import build_embedding_by

    return cache.get_or_create(
        key,
        lambda: _build_managed_model(
            key,
            lambda: build_embedding_by(normalized_provider, config, model=resolved_model)[0],
        ),
    ), key


def clear_model_caches() -> None:
    """Clear both runtime caches for tests or an explicit configuration reload."""
    global _CLIENT_CACHE
    with _CACHE_LOCK:
        if _LLM_CACHE is not None:
            _LLM_CACHE.clear()
        if _EMBEDDING_CACHE is not None:
            _EMBEDDING_CACHE.clear()
        if _CLIENT_CACHE is not None:
            _CLIENT_CACHE.clear()
            _CLIENT_CACHE = None
        _MODEL_LOAD_RECORDS.clear()
