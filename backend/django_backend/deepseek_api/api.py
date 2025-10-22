from ninja import NinjaAPI, Router
from . import services
from django.conf import settings
from .schemas import LoginIn, LoginOut, ChatIn, ChatOut, HistoryOut, ErrorResponse
from .models import APIKey
from .services import get_or_create_session, deepseek_r1_api_call, get_cached_reply, set_cached_reply
from datetime import datetime
import logging
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

@api.post("/login", response={200: LoginOut, 400: ErrorResponse, 403: ErrorResponse})
def login(request, data: LoginIn):
    """
    登录接口：接收用户名和密码，验证后返回 API Key
    密码统一为"secret"，作为示例
    """
    username = data.username.strip()
    password = data.password.strip()

    if not username or not password:
        return 400, {"error": "用户名和密码不能为空"}

    if password != 'secret':
        return 403, {"error": "密码错误"}

    key = services.create_api_key(username)
    return {"api_key": key, "expiry": settings.TOKEN_EXPIRY_SECONDS}

@router.post("/chat", response={200: ChatOut, 401: ErrorResponse, 503: ErrorResponse})
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

    # 5. 调用大模型（带缓存）
    cached_reply = get_cached_reply(query, session_id, user)
    if cached_reply:
        reply = cached_reply
    else:
        # 调用大模型时做保护：如果非 runserver 模式，返回 503 而不是触发加载
        try:
            reply = deepseek_r1_api_call(query)
            set_cached_reply(query, reply, session_id, user)
        except RuntimeError as e:
            # 由 services._get_system 在非 runserver 下抛出
            return 503, {"error": f"服务未启用模型：{str(e)}。请在 runserver 或启用相应开关后再试。"}
    logger.info(f"TopKLogSystem的回复：\n{reply}\n")

    # 6. 保存上下文到会话（更新历史记录）
    session.context += f"用户：{user_input}\n回复：{reply}\n"
    session.save()  # 持久化到数据库

    # 返回与模式一致，仅返回 reply，避免将 prompt 或 query 回显给前端
    return {
        "reply": reply,
    }

# 1. 修复 history 接口
@router.get("/history", response={200: HistoryOut})
def history(request, session_id: str = "default_session"):
    """查看对话历史接口：根据session_id返回对话历史"""
    # 直接使用 session_id 参数，无需通过 data
    processed_session_id = session_id.strip() or "default_session"
    user_api_key = request.auth.key
    session = services.get_or_create_session(processed_session_id, request.auth)
    return {"history": session.context}


# 2. 修复 clear_history 接口
@router.delete("/history", response={200: dict})
def clear_history(request, session_id: str = "default_session"):
    """清空对话历史接口"""
    # 直接使用 session_id 参数，无需通过 data
    processed_session_id = session_id.strip() or "default_session"
    user_api_key = request.auth.key
    session = services.get_or_create_session(processed_session_id, request.auth)
    session.clear_context()
    return {"message": "历史记录已清空"}

# 将路由添加到API
api.add_router("", router)
