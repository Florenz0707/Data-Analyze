import time
import threading
from django.core.cache import cache
import hashlib
from .models import APIKey, RateLimit, ConversationSession
from django.conf import settings

# 线程锁用于速率限制
rate_lock = threading.Lock()

# 与 TopKLogSystem 保持一致的生成流程：
# - 复用同一个 TopKLogSystem 实例，避免每次请求重复构建索引
# - 由 TopKLogSystem 内部读取配置与初始化 LLM/Embedding
try:
    from topklogsystem import TopKLogSystem
    SYSTEM = TopKLogSystem(
        config_path="./config/llm_config.yaml",
    )
except Exception as e:
    # 如果初始化失败，延迟到第一次调用时再尝试
    SYSTEM = None


def _get_system() -> "TopKLogSystem":
    global SYSTEM
    if SYSTEM is None:
        from topklogsystem import TopKLogSystem
        SYSTEM = TopKLogSystem(
            config_path="./config/llm_config.yaml",
        )
    return SYSTEM


def deepseek_r1_api_call(prompt: str) -> str:
    """调用 TopKLogSystem，保持与 topklogsystem.py 一致的生成流程"""
    system = _get_system()
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
        user=user,              # 匹配当前用户（关键！避免跨用户会话冲突）
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
