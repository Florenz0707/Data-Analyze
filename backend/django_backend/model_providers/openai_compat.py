"""OpenAI-compatible Provider adapter."""

from __future__ import annotations

import os
from typing import Any

from .base import BaseProviderAdapter, ModelCapabilities, ProviderMetadata


class OpenAICompatAdapter(BaseProviderAdapter):
    metadata = ProviderMetadata(
        name="openai_compat",
        llm_config_section="OPENAI_COMPAT_CONFIG",
        embedding_config_section="OPENAI_COMPAT_CONFIG",
        llm_model_key="model",
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
        cfg = config.get("OPENAI_COMPAT_CONFIG", {})
        cfg = cfg if isinstance(cfg, dict) else {}
        base_url = cfg.get("base_url") or os.getenv("OPENAI_BASE_URL")
        api_key_env_name = cfg.get("api_key_env_name", "OPENAI_API_KEY")
        api_key = cfg.get("api_key") or os.getenv(api_key_env_name)
        if not api_key:
            raise RuntimeError(f"未找到 API Key: 请设置 {api_key_env_name}")
        return cfg, str(api_key), str(base_url or "")

    def build_llm(self, config: dict[str, Any], *, model: str | None = None) -> Any:
        self._load_dotenv()
        from langchain_openai import ChatOpenAI

        cfg, api_key, base_url = self._api_key(config)
        organization = cfg.get("organization") or os.getenv("OPENAI_ORG")
        client_kwargs: dict[str, Any] = {
            "model": model or cfg.get("model", "gpt-4o-mini"),
            "api_key": api_key,
            "base_url": base_url or None,
            "organization": organization,
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
        from langchain_openai import OpenAIEmbeddings

        cfg, api_key, base_url = self._api_key(config)
        organization = cfg.get("organization") or os.getenv("OPENAI_ORG")
        embedding_name = model or cfg.get("embedding_model", "text-embedding-3-large")
        kwargs: dict[str, Any] = {
            "model": embedding_name,
            "api_key": api_key,
            "base_url": base_url or None,
            "organization": organization,
            "timeout": int(cfg.get("timeout", 60)),
            "max_retries": int(cfg.get("max_retries", 2)),
        }
        if cfg.get("embedding_dimensions"):
            kwargs["dimensions"] = int(cfg["embedding_dimensions"])
        if base_url:
            from deepseek_project.model_runtime import get_cached_http_client

            kwargs["http_client"] = get_cached_http_client(self.name, base_url, config)
        return OpenAIEmbeddings(**kwargs), embedding_name
