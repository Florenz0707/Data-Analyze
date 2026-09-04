"""Built-in Provider Adapter registry."""

from __future__ import annotations

from collections.abc import Iterable

from .base import ProviderAdapter
from .dashscope import DashScopeAdapter
from .ollama import OllamaAdapter
from .openai_compat import OpenAICompatAdapter
from .transformers import TransformersAdapter

_ADAPTERS: dict[str, ProviderAdapter] = {}


def register(adapter: ProviderAdapter) -> None:
    """Register one adapter, rejecting conflicting canonical names."""
    name = adapter.name.strip().lower()
    if not name:
        raise ValueError("Provider adapter name 不能为空")
    if name in _ADAPTERS and _ADAPTERS[name] is not adapter:
        raise ValueError(f"Provider adapter 已注册: {name}")
    _ADAPTERS[name] = adapter


def _register_builtins(adapters: Iterable[ProviderAdapter]) -> None:
    for adapter in adapters:
        register(adapter)
        aliases = (
            adapter.metadata.aliases
            + adapter.metadata.llm_aliases
            + adapter.metadata.embedding_aliases
        )
        for alias in aliases:
            normalized = alias.strip().lower()
            if not normalized:
                raise ValueError(f"Provider adapter alias 不能为空: {adapter.name}")
            existing = _ADAPTERS.get(normalized)
            if existing is not None and existing is not adapter:
                raise ValueError(f"Provider adapter alias 已注册: {normalized}")
            _ADAPTERS[normalized] = adapter


_register_builtins(
    (
        TransformersAdapter(),
        OllamaAdapter(),
        OpenAICompatAdapter(),
        DashScopeAdapter(),
    )
)


def normalize_provider(provider: str) -> str:
    """Return the canonical Provider name for a registered name or alias."""
    normalized = (provider or "").strip().lower()
    try:
        return _ADAPTERS[normalized].name
    except KeyError as exc:
        raise ValueError(f"不支持的 model provider: {provider}") from exc


def get_adapter(provider: str) -> ProviderAdapter:
    """Return the registered adapter for a canonical name or alias."""
    normalized = (provider or "").strip().lower()
    try:
        return _ADAPTERS[normalized]
    except KeyError as exc:
        raise ValueError(f"不支持的 model provider: {provider}") from exc


def registered_providers(*, embedding: bool | None = None) -> frozenset[str]:
    """Return canonical names and aliases supported by the requested role."""
    result: set[str] = set()
    for key, adapter in _ADAPTERS.items():
        metadata = adapter.metadata
        canonical = metadata.name == key
        llm_alias = key in metadata.aliases or key in metadata.llm_aliases
        embedding_alias = key in metadata.aliases or key in metadata.embedding_aliases
        supports_llm = metadata.supports_llm and (canonical or llm_alias)
        supports_embedding = metadata.supports_embedding and (canonical or embedding_alias)
        supported = (
            supports_embedding
            if embedding is True
            else supports_llm
            if embedding is False
            else supports_llm or supports_embedding
        )
        if supported:
            result.add(key)
    return frozenset(result)
