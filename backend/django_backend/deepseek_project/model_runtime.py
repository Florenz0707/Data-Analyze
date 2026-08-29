"""Request-safe model instance caching for provider runtimes."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class ModelInstanceKey:
    """Stable identity for one provider/model/endpoint combination."""

    provider: str
    model: str
    endpoint: str


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
                self._items.move_to_end(key)
                return cached
            instance = factory()
            self._items[key] = instance
            self._items.move_to_end(key)
            while len(self._items) > self.max_size:
                self._items.popitem(last=False)
            return instance

    def clear(self) -> None:
        """Remove all cached instances, primarily for tests and controlled reloads."""
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


_LLM_CACHE: ModelInstanceCache[Any] | None = None
_EMBEDDING_CACHE: ModelInstanceCache[Any] | None = None
_CACHE_LOCK = RLock()


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
    return str(section.get("base_url") or section.get("host") or "")


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
        key, lambda: build_llm_by(normalized_provider, config, model=resolved_model)
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
        key, lambda: build_embedding_by(normalized_provider, config, model=resolved_model)[0]
    ), key


def clear_model_caches() -> None:
    """Clear both runtime caches for tests or an explicit configuration reload."""
    with _CACHE_LOCK:
        if _LLM_CACHE is not None:
            _LLM_CACHE.clear()
        if _EMBEDDING_CACHE is not None:
            _EMBEDDING_CACHE.clear()
