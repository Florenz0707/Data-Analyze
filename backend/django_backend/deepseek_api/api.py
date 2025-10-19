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

@router.post("/chat", response={200: ChatOut, 401: ErrorResponse})
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

    # 4. 拼接上下文（历史记录 + 当前输入）→ 关键！
    # 若 session.context 不为空，说明是旧会话（带历史）
    # 从session获取纯净的对话历史（仅用户输入和回复）
    pure_context = session.context
    # 拼接prompt：纯历史 + 当前用户输入（不含时间戳）
    prompt = pure_context + f"用户：{user_input}\n回复："
    logger.info(f"传递给大模型的prompt：\n{prompt}")  # 调试日志

    # 5. 调用大模型（带完整上下文）
    # 获取缓存时传入session_id和user
    cached_reply = get_cached_reply(prompt, session_id, user)
    if cached_reply:
        reply = cached_reply
    else:
        reply = deepseek_r1_api_call(prompt)
        # 设置缓存时传入session_id和user
        set_cached_reply(prompt, reply, session_id, user)

    # 6. 保存上下文到会话（更新历史记录）
    session.context += f"用户：{user_input}\n回复：{reply}\n"
    session.save()  # 持久化到数据库

    # session.update_context(user_input, reply)

    return {
        "reply": reply,
        # 前端需要的时间戳由前端生成，后端可返回当前时间供参考
        "timestamp": datetime.now().strftime("%H:%M:%S")
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
