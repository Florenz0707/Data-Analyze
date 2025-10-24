import logging

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from ninja import NinjaAPI, Router

from . import services
from .models import APIKey
from .schemas import (
    LoginIn, LoginOut, ChatIn, ChatOut, HistoryOut, ErrorResponse,
    ProvidersOut, LocalModelsOut, SelectLLMIn, SelectLLMOut,
)
from .services import (
    get_or_create_session, get_cached_reply, set_cached_reply,
    get_allowed_providers, get_local_models, set_user_pref, generate_with_user_llm,
)

logger = logging.getLogger(__name__)

api = NinjaAPI(title="DeepSeek-KAI API", version="0.0.1")


def api_key_auth(request):
    """验证请求头中的API Key"""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None  # 未提供认证信息，返回None表示认证失败

    try:
        # 解析格式：Bearer <api_key>
        scheme, key = auth_header.split()
        if scheme.lower() != "bearer":
            return None  # 认证方案错误

        # 验证API Key是否存在
        api_key = APIKey.objects.get(key=key)
        return api_key  # 认证成功，返回APIKey对象
    except (ValueError, APIKey.DoesNotExist):
        return None  # 解析失败或Key不存在，认证失败


router = Router(auth=api_key_auth)


@api.post("/users/register", response={200: LoginIn, 400: ErrorResponse, 409: ErrorResponse})
def register(request, data: LoginIn):
    """
    注册接口：
    1) username 和 password 不为空
    2) username 不能重复
    3) password 使用 Django 内置加密机制存储
    4) 成功时返回 LoginIn 结构体（password 置空）
    """
    username = (data.username or "").strip()
    password = (data.password or "").strip()

    if not username or not password:
        return 400, {"error": "用户名和密码不能为空"}

    if User.objects.filter(username=username).exists():
        return 409, {"error": "用户名已存在"}

    # 创建用户（自动进行密码哈希）
    User.objects.create_user(username=username, password=password)

    # 成功返回 LoginIn 结构体，password 置空
    return {"username": username, "password": ""}


@api.post("/users/login", response={200: LoginOut, 400: ErrorResponse, 403: ErrorResponse})
def login(request, data: LoginIn):
    """
    登录接口：接收用户名和密码，验证后返回 API Key。
    - username/password 必填
    - 使用 Django 认证体系进行密码校验（基于加密存储）
    """
    username = (data.username or "").strip()
    password = (data.password or "").strip()

    if not username or not password:
        return 400, {"error": "用户名和密码不能为空"}

    user = authenticate(request, username=username, password=password)
    if user is None:
        # 避免暴露用户名存在性，统一返回认证失败
        return 403, {"error": "用户名或密码错误"}
    if not getattr(user, "is_active", True):
        return 403, {"error": "账号已被禁用"}

    key = services.create_api_key(username)
    return {"api_key": key, "expiry": settings.TOKEN_EXPIRY_SECONDS}


@router.post("/llm/chat", response={200: ChatOut, 401: ErrorResponse, 503: ErrorResponse})
def chat(request, data: ChatIn):
    # 1. 认证验证（确保用户已登录）
    if not request.auth:
        return 401, {"error": "请先登录获取API Key"}

    # 2. 解析参数（确保 session_id 有效）
    session_id = data.session_id.strip() or "default_session"
    user_input = data.user_input.strip()
    if not user_input:
        return 400, {"error": "请输入消息内容"}

    # 3. 获取会话（加载旧会话或创建新会话）
    user = request.auth  # 从认证获取当前用户（APIKey对象）
    session = get_or_create_session(session_id, user)

    # 4. 直接使用用户问题作为查询，让 TopKLogSystem 使用模板与日志进行生成
    query = user_input
    logger.info(f"传递给TopKLogSystem的query：{query}")

    # 5. 调用大模型（带缓存）。此处使用用户绑定的 LLM（若未设置，则自动创建默认偏好）。
    cached_reply = get_cached_reply(query, session_id, user)
    if cached_reply:
        reply = cached_reply
    else:
        try:
            reply = generate_with_user_llm(user, query)
            set_cached_reply(query, reply, session_id, user)
        except RuntimeError as e:
            return 503, {"error": f"服务未启用模型：{str(e)}。请在 runserver 或启用相应开关后再试。"}
    logger.info(f"TopKLogSystem的回复：\n{reply}\n")

    # 6. 保存上下文到会话（更新历史记录）
    session.context += f"用户：{user_input}\n回复：{reply}\n"
    session.save()

    return {"reply": reply}


# 1. 修复 history 接口
@router.get("/sessions/history", response={200: HistoryOut})
def history(request, session_id: str = "default_session"):
    """查看对话历史接口：根据session_id返回对话历史"""
    # 直接使用 session_id 参数，无需通过 data
    processed_session_id = session_id.strip() or "default_session"
    user_api_key = request.auth.key
    session = services.get_or_create_session(processed_session_id, request.auth)
    return {"history": session.context}


# 2. 修复 clear_history 接口
@router.delete("/sessions/history", response={200: dict})
def clear_history(request, session_id: str = "default_session"):
    """清空对话历史接口"""
    # 直接使用 session_id 参数，无需通过 data
    processed_session_id = session_id.strip() or "default_session"
    user_api_key = request.auth.key
    session = services.get_or_create_session(processed_session_id, request.auth)
    session.clear_context()
    return {"message": "历史记录已清空"}


@router.get("/llm/providers", response={200: ProvidersOut, 401: ErrorResponse})
def get_llm_providers(request):
    if not request.auth:
        return 401, {"error": "未授权"}
    return {"providers": get_allowed_providers()}


@router.get("/llm/local_models", response={200: LocalModelsOut, 401: ErrorResponse})
def get_llm_local_models(request):
    if not request.auth:
        return 401, {"error": "未授权"}
    models = get_local_models()
    # 统一使用 transformers/ollama 键名
    return {"transformers": models.get("transformers", []), "ollama": models.get("ollama", [])}


@router.post("/llm/select", response={200: SelectLLMOut, 400: ErrorResponse, 401: ErrorResponse})
def select_llm(request, data: SelectLLMIn):
    if not request.auth:
        return 401, {"error": "未授权"}
    allowed = set(get_allowed_providers())
    provider = (data.provider or "").lower()
    if provider not in allowed:
        return 400, {"error": f"不允许的 provider: {provider}. 仅允许: {sorted(allowed)}"}
    pref = set_user_pref(request.auth, provider, data.model)
    return {"provider": pref.provider, "model": pref.model or None}


# 将路由添加到API
api.add_router("", router)
