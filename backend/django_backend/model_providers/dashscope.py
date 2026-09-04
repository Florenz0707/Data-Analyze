"""DashScope OpenAI-compatible Provider adapter."""

from __future__ import annotations

import os
from typing import Any

from .base import BaseProviderAdapter, ModelCapabilities, ProviderMetadata


class DashScopeAdapter(BaseProviderAdapter):
    metadata = ProviderMetadata(
        name="dashscope",
        llm_config_section="DASHSCOPE_CONFIG",
        embedding_config_section="DASHSCOPE_CONFIG",
        llm_model_key="chat_model",
        embedding_model_key="embedding_model",
        embedding_dimensions_key="embedding_dimensions",
    )
    capabilities = ModelCapabilities(
        streaming=True,
        structured_output=True,
        tool_calling=True,
        json_mode=True,
    )

    @staticmethod
    def _load_dotenv() -> None:
        from dotenv import load_dotenv

        if os.path.exists("api_key.env"):
            load_dotenv("api_key.env")
        if os.path.exists(".env"):
            load_dotenv(".env")

    @staticmethod
    def _api_key(config: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
        cfg = config.get("DASHSCOPE_CONFIG", {})
        cfg = cfg if isinstance(cfg, dict) else {}
        base_url = cfg.get("base_url") or os.getenv("DASHSCOPE_BASE_URL")
        api_key_env_name = cfg.get("api_key_env_name", "DASHSCOPE_API_KEY")
        api_key = cfg.get("api_key") or os.getenv(api_key_env_name)
        if not api_key:
            raise RuntimeError(f"未找到 API Key: 请设置 {api_key_env_name}")
        return cfg, str(api_key), str(base_url or "")

    def build_llm(self, config: dict[str, Any], *, model: str | None = None) -> Any:
        self._load_dotenv()
        from langchain_openai import ChatOpenAI

        cfg, api_key, base_url = self._api_key(config)
        client_kwargs: dict[str, Any] = {
            "model": model or cfg.get("chat_model", "qwen-turbo"),
            "api_key": api_key,
            "base_url": base_url or None,
            "timeout": int(cfg.get("timeout", 60)),
            "max_retries": int(cfg.get("max_retries", 2)),
        }
        if base_url:
            from deepseek_project.model_runtime import get_cached_http_client

            client_kwargs["http_client"] = get_cached_http_client(self.name, base_url, config)
        return ChatOpenAI(**client_kwargs)

    def build_embedding(
        self, config: dict[str, Any], *, model: str | None = None
    ) -> tuple[Any, str]:
        self._load_dotenv()
        cfg, api_key, base_url = self._api_key(config)
        embedding_name = model or cfg.get("embedding_model", "text-embedding-v4")
        http_client = None
        if base_url:
            from deepseek_project.model_runtime import get_cached_http_client

            http_client = get_cached_http_client(self.name, base_url, config)
        return (
            _make_dashscope_embeddings(
                embedding_name,
                api_key,
                base_url,
                int(cfg.get("timeout", 60)),
                int(cfg.get("max_retries", 2)),
                http_client,
            ),
            embedding_name,
        )


def _make_dashscope_embeddings(
    model: str,
    api_key: str,
    base_url: str,
    timeout: int,
    max_retries: int,
    http_client: Any,
) -> Any:
    from langchain_core.embeddings import Embeddings

    class _DashScopeEmbeddings(Embeddings):
        def __init__(self) -> None:
            from openai import OpenAI

            self.model = model
            client_kwargs: dict[str, Any] = {
                "api_key": api_key,
                "base_url": base_url or None,
                "timeout": timeout,
                "max_retries": max_retries,
            }
            if http_client is not None:
                client_kwargs["http_client"] = http_client
            self.client = OpenAI(**client_kwargs)

        def embed_documents(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
            if not texts:
                return []
            resp = self.client.embeddings.create(model=self.model, input=texts)
            return [item.embedding for item in resp.data]

        def embed_query(self, text: str, **kwargs: Any) -> list[float]:
            resp = self.client.embeddings.create(model=self.model, input=text)
            return resp.data[0].embedding

    return _DashScopeEmbeddings()
