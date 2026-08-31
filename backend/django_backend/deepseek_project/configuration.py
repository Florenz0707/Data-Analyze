"""Configuration loading and validation shared by Django and the RAG runtime."""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUPPORTED_DATABASE_ENGINES = {"sqlite", "mysql", "postgresql"}
SUPPORTED_LLM_PROVIDERS = {"transformers", "ollama", "openai_compat", "dashscope"}
SUPPORTED_EMBEDDING_PROVIDERS = {
    "auto",
    "hf",
    "transformers",
    "ollama",
    "openai_compat",
    "dashscope",
}


class ConfigurationError(ValueError):
    """Raised when application configuration is missing or invalid."""


def _redact_url(value: str) -> str:
    """Remove credentials from a URL while retaining its endpoint information."""
    try:
        parsed = urlsplit(value)
        if parsed.username is None and parsed.password is None:
            return value
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))
    except ValueError:
        return "<redacted-url>"


def redacted_config_summary(config: dict[str, Any]) -> dict[str, Any]:
    """Return a log-safe summary without API keys, tokens, passwords, or proxy credentials."""
    summary_keys = (
        "LLM_PROVIDER",
        "EMBEDDING_PROVIDER",
        "RESPONSE_TOP_K",
        "HISTORY_MODE",
        "HISTORY_MAX_TURNS",
        "HISTORY_TOP_K",
        "HISTORY_SIM_THRESHOLD",
        "HISTORY_MAX_TOKENS",
        "LOG_PATH",
        "SYSTEM_PROMPT_PATH",
        "RESPONSE_TEMPLATE_PATH",
        "VECTOR_STORE_PATH",
    )
    summary = {key: config[key] for key in summary_keys if key in config}
    provider_fields = (
        "model",
        "chat_model",
        "embedding_model",
        "embedding_dimensions",
        "device",
        "embedding_device",
        "base_url",
        "host",
        "port",
        "timeout",
    )
    for section in (
        "TRANSFORMERS_CONFIG",
        "OLLAMA_CONFIG",
        "OPENAI_COMPAT_CONFIG",
        "DASHSCOPE_CONFIG",
    ):
        values = config.get(section)
        if not isinstance(values, dict):
            continue
        section_summary = {
            key: values[key]
            for key in provider_fields
            if key in values and values[key] not in (None, "")
        }
        for key in ("base_url", "host"):
            if key in section_summary and isinstance(section_summary[key], str):
                section_summary[key] = _redact_url(section_summary[key])
        if section_summary:
            summary[section] = section_summary
    return summary


def parse_bool(value: str | None, default: bool) -> bool:
    """Parse a boolean environment value with an explicit default."""
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"布尔配置值无效: {value!r}")


def parse_csv(value: str | None, default: list[str]) -> list[str]:
    """Parse a comma-separated environment value and remove blank entries."""
    if value is None:
        return default.copy()
    return [item.strip() for item in value.split(",") if item.strip()]


def resolve_project_path(value: str | Path, project_root: Path = PROJECT_ROOT) -> Path:
    """Resolve relative application paths from the backend project root."""
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def resolve_config_path(
    filename: str,
    *,
    config_path: str | Path | None = None,
    project_root: Path = PROJECT_ROOT,
) -> Path:
    """Resolve a local config file, falling back to its tracked example.

    A developer-specific file is intentionally ignored by Git. The tracked
    ``*.example`` file keeps a clean checkout runnable and documents the
    complete configuration contract.
    """
    if config_path is not None:
        return resolve_project_path(config_path, project_root)
    local_path = resolve_project_path(f"config/{filename}", project_root)
    if local_path.is_file():
        return local_path
    return local_path.with_name(f"{local_path.name}.example")


def _require_mapping(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key, {})
    if not isinstance(value, dict):
        raise ConfigurationError(f"{key} 必须是对象")
    return value


def _require_model(config: dict[str, Any], section: str, key: str) -> str:
    section_config = _require_mapping(config, section)
    model = section_config.get(key)
    if not isinstance(model, str) or not model.strip():
        raise ConfigurationError(f"{section}.{key} 不能为空")
    return model.strip()


def _validate_provider_models(
    config: dict[str, Any], llm_provider: str, embedding_provider: str
) -> None:
    llm_model_keys = {
        "transformers": ("TRANSFORMERS_CONFIG", "llm_model"),
        "ollama": ("OLLAMA_CONFIG", "model"),
        "openai_compat": ("OPENAI_COMPAT_CONFIG", "model"),
        "dashscope": ("DASHSCOPE_CONFIG", "chat_model"),
    }
    llm_section, llm_key = llm_model_keys[llm_provider]
    _require_model(config, llm_section, llm_key)

    if embedding_provider in {"hf", "transformers"}:
        _require_model(config, "TRANSFORMERS_CONFIG", "embedding_model")
    elif embedding_provider == "ollama":
        _require_model(config, "OLLAMA_CONFIG", "embedding_model")
    elif embedding_provider in {"openai_compat", "dashscope"}:
        embedding_section = _require_mapping(config, embedding_provider.upper() + "_CONFIG")
        _require_model(config, embedding_provider.upper() + "_CONFIG", "embedding_model")
        dimensions = embedding_section.get("embedding_dimensions")
        if dimensions is not None:
            try:
                if int(dimensions) <= 0:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise ConfigurationError(
                    f"{embedding_provider.upper()}_CONFIG.embedding_dimensions 必须是正整数"
                ) from exc


def load_llm_config(
    config_path: str | Path | None = None,
    *,
    project_root: Path = PROJECT_ROOT,
    validate_paths: bool = True,
) -> dict[str, Any]:
    """Load, normalize, and validate the YAML LLM configuration.

    Relative paths are returned as absolute strings rooted at ``project_root``.
    No provider or network client is initialized by this function.
    """
    path = resolve_config_path(
        "llm_config.yaml", config_path=config_path, project_root=project_root
    )
    try:
        with path.open(encoding="utf-8") as handle:
            raw_config = yaml.safe_load(handle) or {}
    except OSError as exc:
        raise ConfigurationError(f"无法读取配置文件: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"配置文件 YAML 无效: {path}") from exc

    if not isinstance(raw_config, dict):
        raise ConfigurationError("LLM 配置根节点必须是对象")
    config = dict(raw_config)

    if "RESPONSE_TOP_K" not in config and "TOP_K" in config:
        config["RESPONSE_TOP_K"] = config["TOP_K"]
        warnings.warn("TOP_K 已废弃，请迁移到 RESPONSE_TOP_K", DeprecationWarning, stacklevel=2)
    try:
        response_top_k = int(config.get("RESPONSE_TOP_K", 10))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("RESPONSE_TOP_K 必须是正整数") from exc
    if not 1 <= response_top_k <= 100:
        raise ConfigurationError("RESPONSE_TOP_K 必须在 1 到 100 之间")
    config["RESPONSE_TOP_K"] = response_top_k

    try:
        reply_cache_ttl = int(config.get("REPLY_CACHE_TTL", 3600))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("REPLY_CACHE_TTL 必须是非负整数") from exc
    if reply_cache_ttl < 0:
        raise ConfigurationError("REPLY_CACHE_TTL 必须是非负整数")
    config["REPLY_CACHE_TTL"] = reply_cache_ttl
    for key, default in {
        "PROMPT_VERSION": "v1",
        "INDEX_VERSION": "v1",
        "CACHE_SCHEMA_VERSION": "v1",
    }.items():
        config[key] = str(config.get(key, default))

    llm_provider = str(config.get("LLM_PROVIDER") or "ollama").strip().lower()
    embedding_provider = str(config.get("EMBEDDING_PROVIDER") or "auto").strip().lower()
    if llm_provider not in SUPPORTED_LLM_PROVIDERS:
        raise ConfigurationError(f"不支持的 LLM provider: {llm_provider}")
    if embedding_provider not in SUPPORTED_EMBEDDING_PROVIDERS:
        raise ConfigurationError(f"不支持的 Embedding provider: {embedding_provider}")
    if embedding_provider == "auto":
        embedding_provider = llm_provider
    config["LLM_PROVIDER"] = llm_provider
    config["EMBEDDING_PROVIDER"] = embedding_provider
    _validate_provider_models(config, llm_provider, embedding_provider)

    for key, default in {
        "LOG_PATH": "data/log",
        "SYSTEM_PROMPT_PATH": "config/system_prompt.yaml",
        "RESPONSE_TEMPLATE_PATH": "config/response_template.md",
        "VECTOR_STORE_PATH": "data/vector_stores",
    }.items():
        config[key] = str(resolve_project_path(config.get(key, default), project_root))

    if validate_paths:
        log_path = Path(config["LOG_PATH"])
        if not log_path.is_dir():
            raise ConfigurationError(f"LOG_PATH 不存在或不是目录: {log_path}")
        for key in ("SYSTEM_PROMPT_PATH", "RESPONSE_TEMPLATE_PATH"):
            if not Path(config[key]).is_file():
                raise ConfigurationError(f"{key} 不存在或不是文件: {config[key]}")

    return config


def _expand_environment_values(value: Any) -> Any:
    """Expand ``${VAR}`` placeholders in YAML scalar values."""
    if isinstance(value, dict):
        return {key: _expand_environment_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_environment_values(item) for item in value]
    if isinstance(value, str):
        return os.path.expandvars(value)
    return value


def _database_engine(value: Any) -> str:
    aliases = {
        "sqlite": "sqlite",
        "sqlite3": "sqlite",
        "django.db.backends.sqlite3": "sqlite",
        "postgres": "postgresql",
        "postgresql": "postgresql",
        "django.db.backends.postgresql": "postgresql",
        "mysql": "mysql",
        "django.db.backends.mysql": "mysql",
    }
    engine = aliases.get(str(value or "").strip().lower())
    if engine is None:
        supported = ", ".join(sorted(SUPPORTED_DATABASE_ENGINES))
        raise ConfigurationError(f"不支持的数据库 ENGINE: {value!r}，可选值: {supported}")
    return engine


def _database_int(value: Any, key: str, default: int) -> int:
    try:
        parsed = int(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"数据库配置 {key} 必须是整数") from exc
    if parsed < 0:
        raise ConfigurationError(f"数据库配置 {key} 必须是非负整数")
    return parsed


def load_database_config(
    config_path: str | Path | None = None,
    *,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    """Load the Django database configuration from a separate YAML file.

    The file contains a top-level ``DATABASE`` mapping. SQLite remains the
    safe default for local development and tests; MySQL and PostgreSQL use
    Django's native backends and require their corresponding Python driver.
    ``${ENV_VAR}`` placeholders are expanded after YAML parsing.
    """
    configured_path = config_path or os.getenv("DJANGO_DB_CONFIG")
    path = resolve_config_path(
        "db_config.yaml", config_path=configured_path, project_root=project_root
    )
    try:
        with path.open(encoding="utf-8") as handle:
            raw_config = _expand_environment_values(yaml.safe_load(handle) or {})
    except OSError as exc:
        raise ConfigurationError(f"无法读取数据库配置文件: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"数据库配置 YAML 无效: {path}") from exc

    if not isinstance(raw_config, dict):
        raise ConfigurationError("数据库配置根节点必须是对象")
    database = raw_config.get("DATABASE", raw_config)
    if not isinstance(database, dict):
        raise ConfigurationError("数据库配置 DATABASE 必须是对象")

    engine = _database_engine(database.get("ENGINE", "sqlite"))
    name = database.get("NAME")
    if engine == "sqlite":
        name = os.getenv("DJANGO_DB_PATH") or name or "db.sqlite3"
        name = str(resolve_project_path(name, project_root))
    elif not isinstance(name, str) or not name.strip():
        raise ConfigurationError("MySQL/PostgreSQL 数据库配置 NAME 不能为空")
    else:
        name = name.strip()

    result: dict[str, Any] = {
        "ENGINE": f"django.db.backends.{'sqlite3' if engine == 'sqlite' else engine}",
        "NAME": name,
        "USER": str(database.get("USER") or ""),
        "PASSWORD": str(database.get("PASSWORD") or ""),
        "HOST": str(database.get("HOST") or ("localhost" if engine != "sqlite" else "")),
        "PORT": str(database.get("PORT") or ("5432" if engine == "postgresql" else "3306"))
        if engine != "sqlite"
        else "",
        "CONN_MAX_AGE": _database_int(database.get("CONN_MAX_AGE"), "CONN_MAX_AGE", 0),
        "CONN_HEALTH_CHECKS": parse_bool(str(database.get("CONN_HEALTH_CHECKS")), False)
        if database.get("CONN_HEALTH_CHECKS") is not None
        else False,
    }
    options = database.get("OPTIONS", {})
    if not isinstance(options, dict):
        raise ConfigurationError("数据库配置 OPTIONS 必须是对象")
    if options:
        result["OPTIONS"] = options
    return result


def env_path(name: str, default: Path, *, base_dir: Path = PROJECT_ROOT) -> Path:
    """Resolve a path-valued environment variable relative to ``base_dir``."""
    return resolve_project_path(os.getenv(name, str(default)), base_dir)
