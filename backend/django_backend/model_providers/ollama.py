"""Ollama Provider adapter."""

from __future__ import annotations

from typing import Any

from .base import BaseProviderAdapter, ModelCapabilities, ProviderMetadata


class OllamaAdapter(BaseProviderAdapter):
    metadata = ProviderMetadata(
        name="ollama",
        llm_config_section="OLLAMA_CONFIG",
        embedding_config_section="OLLAMA_CONFIG",
        llm_model_key="model",
        embedding_model_key="embedding_model",
    )
    capabilities = ModelCapabilities(streaming=True, json_mode=True)

    def build_llm(self, config: dict[str, Any], *, model: str | None = None) -> Any:
        from langchain_ollama import OllamaLLM

        ocfg = self._section(embedding=False, config=config)
        llm_name = self.resolve_model(config, model=model)
        if not llm_name:
            raise ValueError("OLLAMA_CONFIG.model 不能为空")
        kwargs: dict[str, Any] = {"model": llm_name, "temperature": 0.1}
        # M5's server-side contract is JSON-first. The setting remains
        # overridable for providers that do not support Ollama's JSON mode.
        kwargs["format"] = ocfg.get("format", "json")
        if ocfg.get("max_new_tokens") is not None:
            kwargs["num_predict"] = int(ocfg["max_new_tokens"])
        return OllamaLLM(**kwargs)

    def build_embedding(
        self, config: dict[str, Any], *, model: str | None = None
    ) -> tuple[Any, str]:
        from langchain_ollama import OllamaEmbeddings

        embed_name = self.resolve_model(config, embedding=True, model=model)
        if not embed_name:
            raise ValueError("OLLAMA_CONFIG.embedding_model 不能为空")
        return OllamaEmbeddings(model=embed_name), embed_name
