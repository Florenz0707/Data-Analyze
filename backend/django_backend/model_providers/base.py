"""Provider adapter contracts and shared configuration helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ModelCapabilities:
    """Capabilities declared by a provider adapter.

    These flags are descriptive in this migration. They do not change the
    existing RAG or streaming execution path yet.
    """

    streaming: bool = False
    structured_output: bool = False
    tool_calling: bool = False
    json_mode: bool = False


@dataclass(frozen=True)
class ProviderMetadata:
    """Provider configuration metadata shared by Factory, Runtime and validation."""

    name: str
    aliases: tuple[str, ...] = ()
    llm_aliases: tuple[str, ...] = ()
    embedding_aliases: tuple[str, ...] = ()
    llm_config_section: str = ""
    embedding_config_section: str = ""
    llm_model_key: str = ""
    embedding_model_key: str = ""
    embedding_dimensions_key: str | None = None
    supports_llm: bool = True
    supports_embedding: bool = True


class ProviderAdapter(Protocol):
    """Minimal construction contract implemented by each Provider adapter."""

    name: str
    metadata: ProviderMetadata
    capabilities: ModelCapabilities

    def resolve_model(
        self,
        config: dict[str, Any],
        *,
        embedding: bool = False,
        model: str | None = None,
    ) -> str:
        """Resolve a model name using the existing configuration semantics."""

    def cache_identity(self, config: dict[str, Any]) -> str:
        """Return the non-secret endpoint identity used by Runtime cache keys."""

    def build_llm(
        self,
        config: dict[str, Any],
        *,
        model: str | None = None,
    ) -> Any:
        """Construct a LangChain-compatible LLM object."""

    def build_embedding(
        self,
        config: dict[str, Any],
        *,
        model: str | None = None,
    ) -> tuple[Any, str]:
        """Construct a LangChain-compatible embedding object and model name."""


class BaseProviderAdapter:
    """Small shared implementation for metadata-backed model resolution."""

    metadata: ProviderMetadata
    capabilities = ModelCapabilities()

    @property
    def name(self) -> str:
        return self.metadata.name

    def _section(self, *, embedding: bool, config: dict[str, Any]) -> dict[str, Any]:
        section_name = (
            self.metadata.embedding_config_section
            if embedding
            else self.metadata.llm_config_section
        )
        section = config.get(section_name, {})
        return section if isinstance(section, dict) else {}

    def resolve_model(
        self,
        config: dict[str, Any],
        *,
        embedding: bool = False,
        model: str | None = None,
    ) -> str:
        key = self.metadata.embedding_model_key if embedding else self.metadata.llm_model_key
        resolved = model or self._section(embedding=embedding, config=config).get(key)
        return str(resolved or "").strip()

    def cache_identity(self, config: dict[str, Any]) -> str:
        section = self._section(embedding=False, config=config)
        return str(
            section.get("cache_identity") or section.get("base_url") or section.get("host") or ""
        )
