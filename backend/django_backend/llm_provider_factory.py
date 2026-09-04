"""Backward-compatible Factory facade for registered Provider adapters."""

from __future__ import annotations

from typing import Any

from model_providers import get_adapter


def _slugify(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in str(text))[:64]


def build_llm_by(provider: str, env_cfg: dict[str, Any], *, model: str | None = None) -> Any:
    """Build an LLM through the registered Provider adapter."""
    return get_adapter(provider).build_llm(env_cfg, model=model)


def build_embedding_by(
    provider: str, env_cfg: dict[str, Any], *, model: str | None = None
) -> tuple[Any, str]:
    """Build Embeddings through the registered Provider adapter."""
    return get_adapter(provider).build_embedding(env_cfg, model=model)


def build_providers(
    env_cfg: dict[str, Any],
    *,
    llm_model: str | None = None,
    embedding_model: str | None = None,
) -> dict[str, Any]:
    """Build the compatible LLM/Embedding bundle used by the RAG pipeline."""
    llm_provider = str(env_cfg.get("LLM_PROVIDER") or "ollama").strip().lower()
    emb_provider_cfg = str(env_cfg.get("EMBEDDING_PROVIDER") or "auto").strip().lower()
    emb_provider = llm_provider if emb_provider_cfg in ("", "auto") else emb_provider_cfg

    from deepseek_project.model_runtime import get_cached_embedding, get_cached_llm

    llm, llm_key = get_cached_llm(llm_provider, llm_model, env_cfg)
    embedding, embedding_key = get_cached_embedding(emb_provider, embedding_model, env_cfg)
    collection_name = f"log_collection_{_slugify(embedding_key.model)}"

    return {
        "llm": llm,
        "embedding": embedding,
        "collection_name": collection_name,
        "llm_key": llm_key,
        "embedding_key": embedding_key,
    }
