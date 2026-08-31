import base64
import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from deepseek_project.configuration import load_llm_config
from deepseek_project.model_runtime import configured_endpoint, configured_model, get_cached_llm
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q

from .models import (
    APIKey,
    ConversationSession,
    ExternalLLMAPI,
    RateLimit,
    RateLimitBucket,
    RefreshToken,
    UserLLMPreference,
)

# 与 TopKLogSystem 保持一致的生成流程：
# - 复用同一个 TopKLogSystem 实例，避免每次请求重复构建索引
# - 由 TopKLogSystem 内部读取配置与初始化 LLM/Embedding
# 重要：不要在模块导入时实例化模型，避免在 manage.py 的其他命令下也加载模型
SYSTEM = None
_init_lock = threading.Lock()

_REPLY_CACHE_NAMESPACE_KEY = "deepseek:reply-cache:namespace"
_CACHEABLE_GENERATION_PARAMETERS = (
    "max_new_tokens",
    "temperature",
    "top_p",
    "repetition_penalty",
    "do_sample",
)


def _get_system():
    """Return a singleton TopKLogSystem instance.
    Only initialize when running the development server (runserver).
    """
    global SYSTEM
    if SYSTEM is None:
        with _init_lock:
            if SYSTEM is None:
                # 基于 settings 控制是否允许初始化 LLM（适用于 runserver/gunicorn 等所有部署方式）
                if not getattr(settings, "ENABLE_LLM", True):
                    raise RuntimeError("LLM is disabled by settings.ENABLE_LLM=False.")
                from topklogsystem import TopKLogSystem

                SYSTEM = TopKLogSystem(
                    config_path=None,
                )
    return SYSTEM


def preload_system() -> None:
    """Eagerly initialize the TopKLogSystem if in runserver context.
    Safe to call multiple times (idempotent).
    """
    try:
        _get_system()
    except RuntimeError:
        # Non-runserver context: ignore
        pass


def deepseek_r1_api_call(prompt: str) -> str:
    """调用 TopKLogSystem，保持与 topklogsystem.py 一致的生成流程（全局默认 LLM）。"""
    system = _get_system()
    result = system.query(prompt)
    return result.get("response", "")


# ===== 对话历史与相似度选择 =====


def get_history_cfg() -> dict:
    cfg = _load_env_cfg()
    return {
        "mode": (cfg.get("HISTORY_MODE") or "auto").lower(),
        "max_turns": int(cfg.get("HISTORY_MAX_TURNS", 8)),
        "top_k": int(cfg.get("HISTORY_TOP_K", 3)),
        "sim_threshold": float(cfg.get("HISTORY_SIM_THRESHOLD", 0.25)),
        "max_tokens": int(cfg.get("HISTORY_MAX_TOKENS", 1000)),
    }


def parse_session_context(context: str) -> list[tuple[str, str]]:
    """将 ConversationSession.context 解析为 [(user, reply)] 列表。"""
    if not context:
        return []
    lines = context.splitlines()
    turns: list[tuple[str, str]] = []
    cur_u: str | None = None
    cur_a: str | None = None
    for line in lines:
        if line.startswith("用户："):
            if cur_u is not None and cur_a is not None:
                turns.append((cur_u, cur_a))
            cur_u = line[len("用户：") :].strip()
            cur_a = None
        elif line.startswith("回复："):
            cur_a = line[len("回复：") :].strip()
        else:
            # 续行处理：追加到最近的非空段
            if cur_a is not None:
                cur_a += "\n" + line
            elif cur_u is not None:
                cur_u += "\n" + line
    if cur_u is not None and cur_a is not None:
        turns.append((cur_u, cur_a))
    return turns


def _get_embed_model():
    """Return the embedding instance owned by the shared retrieval system."""
    try:
        return getattr(_get_system(), "embedding", None)
    except Exception:
        return None


def _embed_texts(texts: list[str]) -> list[list[float]] | None:
    model = _get_embed_model()
    if not model or not texts:
        return None
    try:
        # 大多数组件支持 .get_text_embedding_batch
        batch_embed = getattr(model, "get_text_embedding_batch", None)
        if callable(batch_embed):
            return batch_embed(texts)
        # 兜底：逐条
        single_embed = getattr(model, "get_text_embedding", None)
        if callable(single_embed):
            return [single_embed(t) for t in texts]
    except Exception:
        return None
    return None


def _cosine(a: list[float], b: list[float]) -> float:
    import math

    if not a or not b:
        return 0.0
    s = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return s / (na * nb)


def _overlap_score(a: str, b: str) -> float:
    """简单启发式：按去停用词后的词集合重叠率计算分数。"""
    import re

    def tokenize(value: str) -> set[str]:
        return set(re.findall(r"[\w\u4e00-\u9fa5]+", (value or "").lower()))

    a_tokens, b_tokens = tokenize(a), tokenize(b)
    if not a_tokens or not b_tokens:
        return 0.0
    inter = len(a_tokens & b_tokens)
    return inter / max(len(a_tokens), len(b_tokens))


def select_history_by_similarity(
    query: str, turns: list[tuple[str, str]], cfg: dict
) -> list[tuple[str, str]]:
    if not turns:
        return []
    # 只取最近 N 轮作为候选
    candidates = turns[-int(cfg.get("max_turns", 8)) :]
    # 优先使用 embedding 相似度
    embed_inputs = [query] + [u + "\n" + a for (u, a) in candidates]
    embs = _embed_texts(embed_inputs)
    scores: list[tuple[float, tuple[str, str]]] = []
    if embs and len(embs) == 1 + len(candidates):
        qv = embs[0]
        for i, turn in enumerate(candidates, start=1):
            scores.append((_cosine(qv, embs[i]), turn))
    else:
        # 回退重叠率
        for turn in candidates:
            scores.append((_overlap_score(query, turn[0] + "\n" + turn[1]), turn))
    # 过滤阈值
    thr = float(cfg.get("sim_threshold", 0.25))
    filtered = [(s, t) for (s, t) in scores if s >= thr]
    # 排序取 top_k
    filtered.sort(key=lambda x: x[0], reverse=True)
    k = int(cfg.get("top_k", 3))
    selected = [t for (_, t) in filtered[:k]]
    return selected


def _truncate_by_chars(text: str, max_tokens: int) -> str:
    # 粗略估算 1 token ~= 0.75 汉字/英文词片段，取保守比例
    max_chars = max(200, int(max_tokens * 4 / 3))
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def compose_prompt_with_history(selected: list[tuple[str, str]], user_input: str, cfg: dict) -> str:
    if not selected:
        return user_input
    budget = int(cfg.get("max_tokens", 1000))
    # 历史拼装（从旧到新）
    lines: list[str] = []
    lines.append("以下为相关的对话历史片段（如无关请忽略）：")
    for u, a in selected:
        frag = f"用户：{u}\n助手：{a}"
        frag = _truncate_by_chars(frag, max_tokens=max(200, budget // max(1, len(selected))))
        lines.append(frag)
        lines.append("---")
    lines.append("当前用户问题：")
    lines.append(user_input)
    lines.append("请在必要时参考上面的历史，否则以当前问题为准，给出准确、简洁的回答。")
    return "\n".join(lines)


# ===== LLM 动态配置（仅 LLM，Embedding 固定）=====


def _load_env_cfg() -> dict:
    try:
        return load_llm_config(validate_paths=False)
    except Exception:
        return {}


def get_allowed_providers() -> list[str]:
    """对外暴露的可选 LLM provider。
    规则：始终包含本地可用的基础后端（transformers、ollama），
    若配置文件中设置了其他后端（openai_compat/dashscope），也一并返回。
    """
    cfg = _load_env_cfg()
    base = ["transformers", "ollama"]
    p = (cfg.get("LLM_PROVIDER") or "").lower()
    extra = [p] if p and p not in base else []
    # 去重并保持顺序：基础优先
    seen = set()
    providers: list[str] = []
    for x in base + extra:
        if x and x not in seen:
            seen.add(x)
            providers.append(x)
    return providers


def get_local_models() -> dict[str, list[str]]:
    """返回本地可用模型的静态配置。
    - 读取 config/available_local_models.json
    - 统一以 transformers/ollama 为键名
    - 文件不存在或解析失败时返回空结构
    JSON 示例：
    {
      "transformers": ["Qwen/Qwen2.5-1.5B-Instruct"],
      "ollama": ["qwen2.5:0.5b"]
    }
    """
    import json
    import os

    base_dir = os.path.dirname(os.path.dirname(__file__))  # django_backend
    cfg_path = os.path.join(base_dir, "config", "available_local_models.json")
    transformers: list[str] = []
    ollama: list[str] = []
    try:
        with open(cfg_path, encoding="utf-8") as f:
            data = json.load(f) or {}
            t = data.get("transformers") or []
            o = data.get("ollama") or []
            if isinstance(t, list):
                transformers = [str(x) for x in t if isinstance(x, (str, int))]
            if isinstance(o, list):
                ollama = [str(x) for x in o if isinstance(x, (str, int))]
    except Exception:
        pass
    return {"transformers": sorted(set(transformers)), "ollama": sorted(set(ollama))}


def _get_default_provider_model() -> tuple[str, str | None]:
    cfg = _load_env_cfg()
    provider = (cfg.get("LLM_PROVIDER") or "").lower()
    model = None
    if provider == "transformers":
        model = (cfg.get("TRANSFORMERS_CONFIG", {}) or {}).get("llm_model")
    elif provider == "ollama":
        model = (cfg.get("OLLAMA_CONFIG", {}) or {}).get("model")
    elif provider == "openai_compat":
        model = (cfg.get("OPENAI_COMPAT_CONFIG", {}) or {}).get("model")
    elif provider == "dashscope":
        model = (cfg.get("DASHSCOPE_CONFIG", {}) or {}).get("chat_model")
    return provider, model


def get_or_create_user_pref(user: APIKey) -> "UserLLMPreference":
    pref = getattr(user, "llm_pref", None)
    if pref:
        return pref
    provider, model = _get_default_provider_model()
    pref = UserLLMPreference.objects.create(user=user, provider=provider or "", model=model or "")
    return pref


def set_user_pref(user: APIKey, provider: str, model: str | None = None) -> "UserLLMPreference":
    pref = get_or_create_user_pref(user)
    pref.provider = (provider or "").lower()
    pref.model = model or ""
    pref.external_api = None
    pref.save(update_fields=["provider", "model", "external_api", "updated_at"])
    return pref


def set_external_user_pref(user: APIKey, external_api: ExternalLLMAPI) -> "UserLLMPreference":
    """Bind a user's preference to a stable external configuration row."""
    pref = get_or_create_user_pref(user)
    pref.provider = "external"
    pref.model = external_api.model_name
    pref.external_api = external_api
    pref.save(update_fields=["provider", "model", "external_api", "updated_at"])
    return pref


def reset_user_pref_to_default(user: APIKey) -> "UserLLMPreference":
    """Move a preference away from a deleted external model."""
    provider, model = _get_default_provider_model()
    return set_user_pref(user, provider, model)


def resolve_external_api(user: APIKey, identifier: str | None) -> ExternalLLMAPI | None:
    """Resolve an alias or model name only within the authenticated user scope."""
    value = (identifier or "").strip()
    if not value:
        return None
    return (
        ExternalLLMAPI.objects.filter(user=user.user)
        .filter(Q(alias__iexact=value) | Q(model_name__iexact=value))
        .order_by("id")
        .first()
    )


def _external_cipher() -> Fernet:
    configured_key = getattr(settings, "EXTERNAL_API_ENCRYPTION_KEY", "")
    if configured_key:
        try:
            return Fernet(configured_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise RuntimeError("EXTERNAL_API_ENCRYPTION_KEY 不是有效的 Fernet 密钥") from exc

    # The Django secret is a stable deployment secret and provides a safe
    # migration path for development installations without a second secret.
    seed = str(settings.SECRET_KEY).encode("utf-8")
    derived_key = base64.urlsafe_b64encode(hashlib.sha256(seed).digest())
    return Fernet(derived_key)


def encrypt_external_api_key(api_key: str) -> str:
    """Encrypt a user-supplied external provider key before persistence."""
    value = (api_key or "").strip()
    if not value:
        raise ValueError("外部模型 API Key 不能为空")
    return _external_cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_external_api_key(encrypted_api_key: str) -> str:
    """Decrypt an external provider key only at the outbound provider boundary."""
    try:
        return _external_cipher().decrypt(encrypted_api_key.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError("外部模型 API Key 无法解密，请检查部署密钥") from exc


def build_llm_for_provider(provider: str, model: str | None = None):
    """Return the cached LLM instance for an explicit provider/model selection."""
    cfg = _load_env_cfg()
    llm, _ = get_cached_llm(provider, model, cfg)
    return llm


def build_llm_for_external_api(external_api: ExternalLLMAPI):
    """Build/cache an OpenAI-compatible client from one user's encrypted row."""
    api_key = decrypt_external_api_key(external_api.api_key_encrypted)
    cfg = _load_env_cfg()
    openai_cfg = dict(cfg.get("OPENAI_COMPAT_CONFIG") or {})
    openai_cfg.update(
        {
            "base_url": external_api.base_url,
            "model": external_api.model_name,
            "api_key": api_key,
            # Credential changes must not reuse a client with the old key.
            "cache_identity": (
                f"{external_api.base_url}#credential-"
                f"{hashlib.sha256(api_key.encode('utf-8')).hexdigest()[:16]}"
            ),
        }
    )
    cfg["LLM_PROVIDER"] = "openai_compat"
    cfg["OPENAI_COMPAT_CONFIG"] = openai_cfg
    llm, _ = get_cached_llm("openai_compat", external_api.model_name, cfg)
    return llm


def generate_with_user_llm(user: APIKey, prompt: str) -> str:
    """Generate with an explicit user-selected LLM without mutating global state."""
    system = _get_system()
    pref = get_or_create_user_pref(user)
    if pref.external_api_id:
        try:
            llm = build_llm_for_external_api(pref.external_api)
        except Exception as exc:
            raise RuntimeError("自定义模型配置不可用") from exc
    else:
        try:
            llm = build_llm_for_provider(pref.provider, pref.model or None)
        except Exception:
            # 内置模型配置失败时保持原有默认回退行为。
            provider, model = _get_default_provider_model()
            llm = build_llm_for_provider(provider, model)
    from llama_index.llms.langchain import LangChainLLM

    result = system.query(prompt, llm=LangChainLLM(llm=llm))
    return result.get("response", "")


def create_api_key(user: str) -> APIKey:
    """Issue or reuse an access token and rotate its refresh-token family."""
    now = int(time.time())
    expiry_seconds = int(settings.TOKEN_EXPIRY_SECONDS)
    refresh_expiry_seconds = int(getattr(settings, "REFRESH_TOKEN_EXPIRY_SECONDS", 30 * 24 * 3600))

    with transaction.atomic():
        existing = (
            APIKey.objects.select_for_update()
            .filter(user=user, revoked_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
        if existing is None:
            api_key = APIKey.objects.create(
                key=APIKey.generate_key(length=64),
                user=user,
                expiry_time=now + expiry_seconds,
            )
            RateLimit.objects.create(
                api_key=api_key,
                reset_time=now + int(settings.RATE_LIMIT_INTERVAL),
            )
            refresh_expiry = now + refresh_expiry_seconds
        else:
            api_key = existing
            api_key.expiry_time = now + expiry_seconds
            api_key.save(update_fields=["expiry_time"])
            current = _get_current_refresh_record(api_key, now)
            refresh_expiry = (
                current.expires_at
                if current is not None and current.expires_at > now
                else now + refresh_expiry_seconds
            )

        _rotate_refresh_token(api_key, now=now, expires_at=refresh_expiry)
        return api_key


def validate_api_key(key_str: str) -> bool:
    """Validate an access token without destroying its refresh-token family."""
    try:
        api_key = APIKey.objects.get(key=key_str)
        return api_key.is_valid()
    except APIKey.DoesNotExist:
        return False


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _get_current_refresh_record(api_key: APIKey, now: int) -> RefreshToken | None:
    """Return the current token record, importing one legacy plaintext value once."""
    current = (
        RefreshToken.objects.select_for_update()
        .filter(api_key=api_key, used_at__isnull=True, revoked_at__isnull=True)
        .order_by("-issued_at")
        .first()
    )
    if current is not None:
        return current

    # Migrate an old APIKey row lazily. New refresh tokens are only stored as
    # hashes in RefreshToken; this keeps existing installations usable.
    legacy = (api_key.refresh_token or "").strip()
    if not legacy:
        return None
    return RefreshToken.objects.create(
        api_key=api_key,
        token_hash=_hash_token(legacy),
        issued_at=now,
        expires_at=int(api_key.refresh_expiry_time or now),
    )


def _rotate_refresh_token(api_key: APIKey, *, now: int, expires_at: int) -> str:
    """Consume the current refresh token and issue a new token in its family."""
    current = _get_current_refresh_record(api_key, now)
    family_id = current.family_id if current is not None else uuid.uuid4()
    raw_token = APIKey.generate_refresh_token(length=96)
    token_hash = _hash_token(raw_token)
    next_record = RefreshToken.objects.create(
        api_key=api_key,
        token_hash=token_hash,
        family_id=family_id,
        issued_at=now,
        expires_at=expires_at,
    )
    if current is not None:
        current.used_at = now
        current.replaced_by_hash = next_record.token_hash
        current.save(update_fields=["used_at", "replaced_by_hash"])

    api_key.refresh_token = None
    api_key.refresh_expiry_time = expires_at
    api_key.save(update_fields=["refresh_token", "refresh_expiry_time"])
    # The raw value is intentionally transient: API serialization needs it,
    # while the database keeps only a digest for newly issued tokens.
    api_key.refresh_token = raw_token
    return raw_token


def refresh_access_token(refresh_token: str) -> APIKey | None:
    """Rotate a refresh token once and revoke its family on reuse."""
    now = int(time.time())
    token_hash = _hash_token((refresh_token or "").strip())
    if not refresh_token:
        return None

    with transaction.atomic():
        record = (
            RefreshToken.objects.select_for_update()
            .select_related("api_key")
            .filter(token_hash=token_hash)
            .first()
        )
        if record is None:
            # Compatibility path for rows created before the hash-backed table.
            api_key = (
                APIKey.objects.select_for_update()
                .filter(refresh_token=refresh_token, revoked_at__isnull=True)
                .first()
            )
            if api_key is None:
                return None
            record = _get_current_refresh_record(api_key, now)
            if record is None or record.token_hash != token_hash:
                return None
        api_key = APIKey.objects.select_for_update().get(pk=record.api_key_id)

        if record.used_at is not None or record.revoked_at is not None:
            _revoke_refresh_family(record.family_id, api_key, now)
            return None
        if now >= record.expires_at or api_key.revoked_at is not None:
            record.revoked_at = now
            record.save(update_fields=["revoked_at"])
            return None

        # Rotate the access token as well, so a token presented before the
        # refresh cannot remain valid after the refresh exchange.
        api_key.key = APIKey.generate_key(length=64)
        api_key.expiry_time = now + int(settings.TOKEN_EXPIRY_SECONDS)
        api_key.save(update_fields=["key", "expiry_time"])
        _rotate_refresh_token(api_key, now=now, expires_at=record.expires_at)
        return api_key


def _revoke_refresh_family(family_id, api_key: APIKey, now: int) -> None:
    RefreshToken.objects.filter(family_id=family_id, revoked_at__isnull=True).update(revoked_at=now)
    api_key.revoked_at = now
    api_key.save(update_fields=["revoked_at"])


def revoke_tokens(*, refresh_token: str | None = None, access_token: str | None = None) -> None:
    """Revoke the access token and refresh-token family associated with a logout."""
    now = int(time.time())
    with transaction.atomic():
        api_key = None
        if access_token:
            api_key = APIKey.objects.select_for_update().filter(key=access_token).first()
        if api_key is None and refresh_token:
            record = (
                RefreshToken.objects.select_for_update()
                .filter(token_hash=_hash_token(refresh_token))
                .first()
            )
            if record is not None:
                api_key = APIKey.objects.select_for_update().get(pk=record.api_key_id)
                _revoke_refresh_family(record.family_id, api_key, now)
            else:
                # Compatibility path for a legacy APIKey that has not yet
                # been lazily imported into RefreshToken.
                api_key = (
                    APIKey.objects.select_for_update().filter(refresh_token=refresh_token).first()
                )
        if api_key is not None:
            api_key.revoked_at = now
            api_key.save(update_fields=["revoked_at"])
            RefreshToken.objects.filter(api_key=api_key, revoked_at__isnull=True).update(
                revoked_at=now
            )


def check_rate_limit(key_str: str) -> bool:
    """兼容旧调用方，并使用数据库共享窗口替代进程内线程锁。"""
    if not APIKey.objects.filter(key=key_str).exists():
        return False
    return consume_rate_limits(
        "legacy_api",
        [("api_key", key_str)],
        limit=int(settings.RATE_LIMIT_MAX),
        interval=int(settings.RATE_LIMIT_INTERVAL),
    ).allowed


@dataclass(frozen=True)
class RateLimitDecision:
    """The outcome of consuming one or more dimensions of a rate limit."""

    allowed: bool
    retry_after: int
    remaining: int
    reset_at: int


def _rate_limit_subject(dimension: str, value: str) -> str:
    """Keep usernames and IP addresses out of the bucket table."""
    raw = f"{dimension}:{value}".encode()
    return hashlib.sha256(raw).hexdigest()


def get_client_ip(request) -> str:
    """Resolve the client IP, trusting forwarded headers only when configured."""
    meta = getattr(request, "META", {})
    if getattr(settings, "RATE_LIMIT_TRUST_PROXY", False):
        forwarded = meta.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip() or "unknown"
    return meta.get("REMOTE_ADDR", "unknown")


def get_rate_limit_policy(scope: str) -> tuple[int, int]:
    """Return the configured ``(limit, interval)`` pair for a request scope."""
    suffix = {
        "login": "LOGIN",
        "refresh": "REFRESH",
        "chat": "CHAT",
        "model_validate": "MODEL_VALIDATE",
        "api": "API",
    }.get(scope)
    if suffix is None:
        raise ValueError(f"Unknown rate-limit scope: {scope}")
    return (
        int(getattr(settings, f"RATE_LIMIT_{suffix}_MAX", settings.RATE_LIMIT_MAX)),
        int(getattr(settings, f"RATE_LIMIT_{suffix}_INTERVAL", settings.RATE_LIMIT_INTERVAL)),
    )


def consume_rate_limits(
    scope: str,
    subjects: list[tuple[str, str]],
    *,
    limit: int,
    interval: int,
    now: int | None = None,
) -> RateLimitDecision:
    """Atomically consume a fixed-window bucket for all supplied dimensions.

    The counters live in the configured Django database, so row locks remain
    effective across threads and worker processes when using PostgreSQL/MySQL.
    """
    if limit <= 0 or interval <= 0:
        raise ValueError("Rate-limit limit and interval must be positive")
    current = int(time.time()) if now is None else int(now)
    window_start = current // interval * interval
    reset_at = window_start + interval
    unique_subjects = list(dict.fromkeys(subjects))
    if not unique_subjects:
        raise ValueError("At least one rate-limit subject is required")

    with transaction.atomic():
        buckets: list[RateLimitBucket] = []
        for dimension, value in sorted(unique_subjects):
            subject = _rate_limit_subject(dimension, str(value))
            bucket, _ = RateLimitBucket.objects.get_or_create(
                scope=scope,
                subject=subject,
                window_start=window_start,
                defaults={"count": 0},
            )
            buckets.append(RateLimitBucket.objects.select_for_update().get(pk=bucket.pk))

        if any(bucket.count >= limit for bucket in buckets):
            return RateLimitDecision(
                allowed=False,
                retry_after=max(1, reset_at - current),
                remaining=0,
                reset_at=reset_at,
            )

        for bucket in buckets:
            bucket.count += 1
            bucket.save(update_fields=["count"])

        remaining = min(limit - bucket.count for bucket in buckets)
        return RateLimitDecision(
            allowed=True,
            retry_after=0,
            remaining=remaining,
            reset_at=reset_at,
        )


def enforce_request_rate_limit(request, scope: str, user: str | None = None) -> RateLimitDecision:
    """Consume the user/IP dimensions for an authenticated or public request."""
    limit, interval = get_rate_limit_policy(scope)
    subjects = [("ip", get_client_ip(request))]
    if user:
        subjects.append(("user", user))
    return consume_rate_limits(scope, subjects, limit=limit, interval=interval)


def get_or_create_session(session_id: str, user: APIKey) -> ConversationSession:
    """
    获取或创建用户的专属会话：
    - 若用户+session_id已存在 → 加载旧会话（保留历史）
    - 若不存在 → 创建新会话（空历史）
    注意：会话与 username 关联。
    """
    username = user.user
    session, created = ConversationSession.objects.get_or_create(
        session_id=session_id,  # 匹配会话ID
        user=username,  # 与用户名关联
        defaults={"context": ""},
    )
    # 调试日志：确认是否创建新会话（created=True 表示新会话）
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"会话 {session_id}（用户：{username}）{'创建新会话' if created else '加载旧会话'}")
    return session


def get_cached_reply(
    prompt: str,
    session_id: str,
    user: APIKey,
    *,
    provider: str | None = None,
    model: str | None = None,
    parameters: dict[str, Any] | None = None,
    history: list[tuple[str, str]] | None = None,
) -> str | None:
    """Read a valid reply using the complete request and runtime cache identity."""
    cache_key = _build_reply_cache_key(
        prompt,
        session_id,
        user,
        provider=provider,
        model=model,
        parameters=parameters,
        history=history,
    )
    value = cache.get(cache_key)
    if isinstance(value, str) and value.strip():
        return value
    if value is not None:
        cache.delete(cache_key)
    return None


def set_cached_reply(
    prompt: str,
    reply: str,
    session_id: str,
    user: APIKey,
    timeout: int | None = None,
    *,
    provider: str | None = None,
    model: str | None = None,
    parameters: dict[str, Any] | None = None,
    history: list[tuple[str, str]] | None = None,
    cacheable: bool = True,
) -> bool:
    """Write a successful reply with configured TTL; return whether it was stored."""
    if not cacheable or not isinstance(reply, str) or not reply.strip():
        return False
    cfg = _load_env_cfg()
    if timeout is None:
        timeout = int(cfg.get("REPLY_CACHE_TTL", 3600))
    if timeout <= 0:
        return False
    cache_key = _build_reply_cache_key(
        prompt,
        session_id,
        user,
        provider=provider,
        model=model,
        parameters=parameters,
        history=history,
    )
    cache.set(cache_key, reply, timeout)
    return True


def invalidate_reply_cache() -> str:
    """Rotate the shared reply-cache namespace so all previous replies become stale."""
    namespace = uuid.uuid4().hex
    cache.set(_REPLY_CACHE_NAMESPACE_KEY, namespace, timeout=None)
    return namespace


def _get_reply_cache_namespace() -> str:
    namespace = cache.get(_REPLY_CACHE_NAMESPACE_KEY)
    if isinstance(namespace, str) and namespace:
        return namespace
    namespace = "initial"
    cache.add(_REPLY_CACHE_NAMESPACE_KEY, namespace, timeout=None)
    return str(cache.get(_REPLY_CACHE_NAMESPACE_KEY) or namespace)


def _get_generation_parameters(config: dict[str, Any], provider: str) -> dict[str, Any]:
    section_name = {
        "transformers": "TRANSFORMERS_CONFIG",
        "ollama": "OLLAMA_CONFIG",
        "openai_compat": "OPENAI_COMPAT_CONFIG",
        "dashscope": "DASHSCOPE_CONFIG",
    }.get(provider)
    section = config.get(section_name, {}) if section_name else {}
    if not isinstance(section, dict):
        return {}
    return {key: section[key] for key in _CACHEABLE_GENERATION_PARAMETERS if key in section}


def _build_reply_cache_key(
    prompt: str,
    session_id: str,
    user: APIKey,
    *,
    provider: str | None = None,
    model: str | None = None,
    parameters: dict[str, Any] | None = None,
    history: list[tuple[str, str]] | None = None,
) -> str:
    """Build a stable SHA-256 key from all inputs that can change a reply."""
    cfg = _load_env_cfg()
    normalized_provider = (provider or cfg.get("LLM_PROVIDER") or "").lower()
    resolved_model = model or configured_model(cfg, normalized_provider)
    request_parameters = dict(_get_generation_parameters(cfg, normalized_provider))
    request_parameters.update(parameters or {})
    identity = {
        "user": user.user,
        "session": session_id,
        "prompt": prompt,
        "history": history or [],
        "parameters": request_parameters,
        "provider": normalized_provider,
        "model": resolved_model,
        "endpoint": configured_endpoint(cfg, normalized_provider) if normalized_provider else "",
        "prompt_version": cfg.get("PROMPT_VERSION", "default"),
        "index_version": cfg.get("INDEX_VERSION", "default"),
        "response_top_k": cfg.get("RESPONSE_TOP_K", 10),
        "cache_schema_version": cfg.get("CACHE_SCHEMA_VERSION", "v1"),
        "cache_namespace": _get_reply_cache_namespace(),
    }
    identity_text = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"reply:{generate_cache_key(identity_text)}"


def generate_cache_key(original_key: str) -> str:
    """
    生成安全的缓存键。
    对原始字符串进行哈希处理，确保键长度固定且仅包含安全字符。
    """
    # 使用 SHA-256 哈希函数生成固定长度的键（64 位十六进制字符串）。
    hash_obj = hashlib.sha256(original_key.encode("utf-8"))
    return hash_obj.hexdigest()
