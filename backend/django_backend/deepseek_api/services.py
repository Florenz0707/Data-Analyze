import hashlib
import os
import threading
import time
from typing import List, Dict, Tuple

import yaml
from django.conf import settings
from django.core.cache import cache

from .models import APIKey, RateLimit, ConversationSession, UserLLMPreference

# 线程锁用于速率限制
rate_lock = threading.Lock()

# 与 TopKLogSystem 保持一致的生成流程：
# - 复用同一个 TopKLogSystem 实例，避免每次请求重复构建索引
# - 由 TopKLogSystem 内部读取配置与初始化 LLM/Embedding
# 重要：不要在模块导入时实例化模型，避免在 manage.py 的其他命令下也加载模型
SYSTEM = None
_init_lock = threading.Lock()


def _get_system() -> "TopKLogSystem":
    """Return a singleton TopKLogSystem instance.
    Only initialize when running the development server (runserver).
    """
    global SYSTEM
    if SYSTEM is None:
        with _init_lock:
            if SYSTEM is None:
                # 基于 settings 控制是否允许初始化 LLM（适用于 runserver/gunicorn 等所有部署方式）
                if not getattr(settings, 'ENABLE_LLM', True):
                    raise RuntimeError("LLM is disabled by settings.ENABLE_LLM=False.")
                from topklogsystem import TopKLogSystem
                SYSTEM = TopKLogSystem(
                    config_path="./config/llm_config.yaml",
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


# ===== LLM 动态配置（仅 LLM，Embedding 固定）=====

def _load_env_cfg() -> Dict:
    cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "llm_config.yaml")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def get_allowed_providers() -> List[str]:
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
    providers: List[str] = []
    for x in base + extra:
        if x and x not in seen:
            seen.add(x)
            providers.append(x)
    return providers


def get_local_models() -> Dict[str, List[str]]:
    """本地可用模型列表（仅供本地管理 API）：HF 缓存模型 + Ollama 列表。"""
    # HF 缓存
    hf_list: List[str] = []
    try:
        from huggingface_hub import scan_cache
        import os
        cache_dir = os.getenv("HF_HOME") or os.path.expanduser("~/.cache/huggingface")
        info = scan_cache(cache_dir=cache_dir)
        for repo in info.repos:
            rid = f"{repo.repo_owner}/{repo.repo_name}" if repo.repo_owner else repo.repo_name
            if repo.repo_type == "model":
                hf_list.append(rid)
    except Exception:
        pass

    # Ollama 模型
    ollama_list: List[str] = []
    try:
        import requests
        cfg = _load_env_cfg()
        ocfg = cfg.get("OLLAMA_CONFIG", {})
        host = ocfg.get("host", "http://localhost:11434").rstrip("/")
        url = f"{host}/api/tags"
        r = requests.get(url, timeout=3)
        if r.ok:
            data = r.json() or {}
            for m in data.get("models", []):
                name = m.get("name")
                if name:
                    ollama_list.append(name)
    except Exception:
        pass

    return {"hf": sorted(set(hf_list)), "ollama": sorted(set(ollama_list))}


def _get_default_provider_model() -> Tuple[str, str | None]:
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
    pref.save()
    return pref


def build_llm_for_provider(provider: str):
    from llm_provider_factory import build_llm_by
    cfg = _load_env_cfg()
    return build_llm_by(provider, cfg)


def generate_with_user_llm(user: APIKey, prompt: str) -> str:
    """在请求级上下文中覆盖 Settings.llm，避免并发用户串台。"""
    system = _get_system()
    pref = get_or_create_user_pref(user)
    try:
        llm = build_llm_for_provider(pref.provider)
    except Exception:
        # 回退到默认
        provider, _ = _get_default_provider_model()
        llm = build_llm_for_provider(provider)
    from llama_index.llms.langchain import LangChainLLM
    from llama_index.core import Settings as LISettings
    with LISettings.as_context(llm=LangChainLLM(llm=llm)):
        result = system.query(prompt)
        return result.get("response", "")


def create_api_key(user: str) -> str:
    """创建 API Key 并保存到数据库"""
    key = APIKey.generate_key()
    expiry = time.time() + settings.TOKEN_EXPIRY_SECONDS

    api_key = APIKey.objects.create(
        key=key,
        user=user,
        expiry_time=expiry
    )

    # 创建对应的速率限制记录
    RateLimit.objects.create(
        api_key=api_key,
        reset_time=time.time() + settings.RATE_LIMIT_INTERVAL
    )

    return key


def validate_api_key(key_str: str) -> bool:
    """验证 API Key 是否存在且未过期"""
    try:
        api_key = APIKey.objects.get(key=key_str)
        if api_key.is_valid():
            return True
        else:
            api_key.delete()  # 删除过期key
            return False
    except APIKey.DoesNotExist:
        return False


def check_rate_limit(key_str: str) -> bool:
    """检查 API Key 的请求频率是否超过限制"""
    with rate_lock:
        try:
            rate_limit = RateLimit.objects.select_related('api_key').get(api_key__key=key_str)

            current_time = time.time()
            if current_time > rate_limit.reset_time:
                rate_limit.count = 1
                rate_limit.reset_time = current_time + settings.RATE_LIMIT_INTERVAL
                rate_limit.save()
                return True
            elif rate_limit.count < settings.RATE_LIMIT_MAX:
                rate_limit.count += 1
                rate_limit.save()
                return True
            else:
                return False
        except RateLimit.DoesNotExist:
            # 如果速率限制记录不存在，创建一个新的
            try:
                current_time = time.time()
                api_key = APIKey.objects.get(key=key_str)
                RateLimit.objects.create(
                    api_key=api_key,
                    count=1,
                    reset_time=current_time + settings.RATE_LIMIT_INTERVAL
                )
                return True
            except APIKey.DoesNotExist:
                return False


def get_or_create_session(session_id: str, user: APIKey) -> ConversationSession:
    """
    获取或创建用户的专属会话：
    - 若用户+session_id已存在 → 加载旧会话（保留历史）
    - 若不存在 → 创建新会话（空历史）
    """
    session, created = ConversationSession.objects.get_or_create(
        session_id=session_id,  # 匹配会话ID
        user=user,  # 匹配当前用户（关键！避免跨用户会话冲突）
        defaults={'context': ''}
    )
    # 调试日志：确认是否创建新会话（created=True 表示新会话）
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"会话 {session_id}（用户：{user.user}）{'创建新会话' if created else '加载旧会话'}")
    return session


def get_cached_reply(prompt: str, session_id: str, user: APIKey) -> str | None:
    """缓存键包含 session_id 和 user，避免跨会话冲突"""
    cache_key = f"reply:{user.user}:{session_id}:{hash(prompt)}"
    return cache.get(cache_key)


def set_cached_reply(prompt: str, reply: str, session_id: str, user: APIKey, timeout=3600):
    cache_key = f"reply:{user.user}:{session_id}:{hash(prompt)}"
    cache.set(cache_key, reply, timeout)


def generate_cache_key(original_key: str) -> str:
    """
    生成安全的缓存键。
    对原始字符串进行哈希处理，确保键长度固定且仅包含安全字符。
    """
    # 使用SHA256哈希函数生成固定长度的键（64位十六进制字符串）
    hash_obj = hashlib.sha256(original_key.encode('utf-8'))
    return hash_obj.hexdigest()
