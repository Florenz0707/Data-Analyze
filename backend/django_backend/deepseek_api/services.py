import hashlib
import os
import threading
import time
from typing import List, Dict, Tuple, Optional

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


def _get_system():
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


# ===== 对话历史与相似度选择 =====

def get_history_cfg() -> Dict:
    cfg = _load_env_cfg()
    return {
        "mode": (cfg.get("HISTORY_MODE") or "auto").lower(),
        "max_turns": int(cfg.get("HISTORY_MAX_TURNS", 8)),
        "top_k": int(cfg.get("HISTORY_TOP_K", 3)),
        "sim_threshold": float(cfg.get("HISTORY_SIM_THRESHOLD", 0.25)),
        "max_tokens": int(cfg.get("HISTORY_MAX_TOKENS", 1000)),
    }


def parse_session_context(context: str) -> List[Tuple[str, str]]:
    """将 ConversationSession.context 解析为 [(user, reply)] 列表。"""
    if not context:
        return []
    lines = context.splitlines()
    turns: List[Tuple[str, str]] = []
    cur_u: Optional[str] = None
    cur_a: Optional[str] = None
    for line in lines:
        if line.startswith("用户："):
            if cur_u is not None and cur_a is not None:
                turns.append((cur_u, cur_a))
            cur_u = line[len("用户："):].strip()
            cur_a = None
        elif line.startswith("回复："):
            cur_a = line[len("回复："):].strip()
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
    """从 llama_index Settings 取 embed_model（TopKLogSystem 初始化时应已配置）。"""
    try:
        from llama_index.core import Settings as LISettings
        return getattr(LISettings, "embed_model", None)
    except Exception:
        return None


def _embed_texts(texts: List[str]) -> Optional[List[List[float]]]:
    model = _get_embed_model()
    if not model or not texts:
        return None
    try:
        # 大多数组件支持 .get_text_embedding_batch
        if hasattr(model, "get_text_embedding_batch"):
            return model.get_text_embedding_batch(texts)
        # 兜底：逐条
        if hasattr(model, "get_text_embedding"):
            return [model.get_text_embedding(t) for t in texts]
    except Exception:
        return None
    return None


def _cosine(a: List[float], b: List[float]) -> float:
    import math
    if not a or not b:
        return 0.0
    s = sum(x*y for x, y in zip(a, b))
    na = math.sqrt(sum(x*x for x in a))
    nb = math.sqrt(sum(y*y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return s / (na * nb)


def _overlap_score(a: str, b: str) -> float:
    """简单启发式：按去停用词后的词集合重叠率计算分数。"""
    import re
    tok = lambda s: set(re.findall(r"[\w\u4e00-\u9fa5]+", (s or "").lower()))
    A, B = tok(a), tok(b)
    if not A or not B:
        return 0.0
    inter = len(A & B)
    return inter / max(len(A), len(B))


def select_history_by_similarity(query: str, turns: List[Tuple[str, str]], cfg: Dict) -> List[Tuple[str, str]]:
    if not turns:
        return []
    # 只取最近 N 轮作为候选
    candidates = turns[-int(cfg.get("max_turns", 8)) :]
    # 优先使用 embedding 相似度
    embed_inputs = [query] + [u + "\n" + a for (u, a) in candidates]
    embs = _embed_texts(embed_inputs)
    scores: List[Tuple[float, Tuple[str, str]]] = []
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


def compose_prompt_with_history(selected: List[Tuple[str, str]], user_input: str, cfg: Dict) -> str:
    if not selected:
        return user_input
    budget = int(cfg.get("max_tokens", 1000))
    # 历史拼装（从旧到新）
    lines: List[str] = []
    lines.append("以下为相关的对话历史片段（如无关请忽略）：")
    for (u, a) in selected:
        frag = f"用户：{u}\n助手：{a}"
        frag = _truncate_by_chars(frag, max_tokens=max(200, budget // max(1, len(selected))))
        lines.append(frag)
        lines.append("---")
    lines.append("当前用户问题：")
    lines.append(user_input)
    lines.append("请在必要时参考上面的历史，否则以当前问题为准，给出准确、简洁的回答。")
    return "\n".join(lines)


# ===== LLM 动态配置（仅 LLM，Embedding 固定）=====

def _load_env_cfg() -> Dict:
    cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "llm_config.yaml")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        return {}
    # 兼容旧字段：TOP_K -> RESPONSE_TOP_K
    if "RESPONSE_TOP_K" not in cfg and "TOP_K" in cfg:
        cfg["RESPONSE_TOP_K"] = cfg.get("TOP_K")
    return cfg


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
    transformers: List[str] = []
    ollama: List[str] = []
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
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
    # 某些版本的 llama_index 不提供 Settings.as_context，这里采用手动覆盖并回滚
    old_llm = getattr(LISettings, "llm", None)
    try:
        LISettings.llm = LangChainLLM(llm=llm)
        result = system.query(prompt)
        return result.get("response", "")
    finally:
        LISettings.llm = old_llm


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
