import logging

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from ninja import NinjaAPI, Router
from django.utils import timezone
from django.db.models import Q

from . import services
from .models import APIKey, ConversationSession, ExternalLLMAPI, Session, History
from .schemas import (
    LoginIn, ChatIn, ChatOut, HistoryListOut, ErrorResponse,
    ProvidersOut, LocalModelsOut, SelectLLMIn, SelectLLMOut,
    SessionIn, SessionOut, SessionListOut,
    APIIn, ModelsListOut, ModelIn,
)
from .services import (
    get_or_create_session, get_cached_reply, set_cached_reply,
    get_allowed_providers, get_local_models, set_user_pref, generate_with_user_llm,
    get_history_cfg, parse_session_context, select_history_by_similarity, compose_prompt_with_history,
)


logger = logging.getLogger(__name__)

api = NinjaAPI(title="DeepSeek-KAI API", version="0.1.0")


def _validate_openai_compat(base_url: str, api_key: str, model_name: str) -> bool:
    """Quickly validate an OpenAI-compatible chat endpoint with a 1-token request."""
    try:
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=15)
        # minimal probe
        resp = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            temperature=0.0,
        )
        # if no exception, treat as OK
        return bool(resp and getattr(resp, "id", None))
    except Exception as e:
        logger.warning(f"Validate external API failed: {e}")
        return False


def api_key_auth(request):
    """验证请求头中的API Key，并进行过期校验：
    - 若已过期，删除记录并拒绝
    - 若有效，返回 APIKey 实例
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None
    try:
        scheme, key = auth_header.split()
        if scheme.lower() != "bearer":
            return None
        api_key = APIKey.objects.get(key=key)
        # 过期校验
        import time
        if int(time.time()) >= int(api_key.expiry_time or 0):
            # 删除过期 key
            api_key.delete()
            return None
        return api_key
    except (ValueError, APIKey.DoesNotExist):
        return None


router = Router(auth=api_key_auth)


@api.post("/users/register", response={200: dict, 400: ErrorResponse, 409: ErrorResponse})
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
    return {"message": "注册成功"}


@api.post("/users/login", response={200: dict, 400: ErrorResponse, 403: ErrorResponse})
def login(request, data: LoginIn):
    """
    登录接口：
    - 若该用户名存在未过期 api_key，则刷新其有效期并发放同一 key
    - 否则创建新的 api_key 与 refresh_token
    - 同时返回 refresh_token（其有效期固定，不随刷新延长）
    """
    username = (data.username or "").strip()
    password = (data.password or "").strip()

    if not username or not password:
        return 400, {"error": "用户名和密码不能为空"}

    user = authenticate(request, username=username, password=password)
    if user is None:
        return 403, {"error": "用户名或密码错误"}
    if not getattr(user, "is_active", True):
        return 403, {"error": "账号已被禁用"}

    api_key_obj = services.create_api_key(username)
    payload = {
        "message": "登录成功",
    }
    response = api.create_response(request, payload, status=200)
    # 将 access token 放在响应头（便于前端从 header 读取）
    response["Authorization"] = f"Bearer {api_key_obj.key}"
    # 将 refresh_token 写入 HttpOnly Cookie
    response.set_cookie(
        key="refresh_token",
        value=api_key_obj.refresh_token or "",
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
        max_age=getattr(settings, "REFRESH_TOKEN_EXPIRY_SECONDS", None),
        path="/",
    )
    return response


@router.post("/llm/chat", response={200: ChatOut, 401: ErrorResponse, 503: ErrorResponse})
def chat(request, data: ChatIn):
    # 1. 认证验证（确保用户已登录）
    if not request.auth:
        return 401, {"error": "请先登录获取API Key"}

    # 2. 解析参数（确保 session_id 有效）
    sid = (data.session_id or "").strip() or "default_session"
    user_input = (data.user_input or "").strip()
    if not user_input:
        return 400, {"error": "请输入消息内容"}

    # 3. 获取或创建新会话（基于新表 Session）
    username = request.auth.user
    session, _ = Session.objects.get_or_create(session_id=sid, user=username)

    # 4. 构造历史上下文（基于新表 History）
    hist_cfg = get_history_cfg()
    use_history_mode = (data.use_history or hist_cfg.get("mode") or "auto").lower()
    qs = History.objects.filter(session_id=sid, user=username).order_by("created_at", "id")
    turns_all = [(h.user_input or "", h.response or "") for h in qs]
    if use_history_mode == "on":
        selected = turns_all[-int(hist_cfg.get("max_turns", 8)):]
    elif use_history_mode == "auto":
        selected = select_history_by_similarity(user_input, turns_all, hist_cfg)
    else:
        selected = []
    query = compose_prompt_with_history(selected, user_input, hist_cfg)
    logger.info(f"传递给TopKLogSystem的query（含历史{len(selected)}段）：{query}")

    # 5. 调用大模型（带缓存）。此处使用用户绑定的 LLM（若未设置，则自动创建默认偏好）。
    user_obj = request.auth
    cached_reply = get_cached_reply(query, sid, user_obj)
    if cached_reply:
        reply = cached_reply
    else:
        try:
            reply = generate_with_user_llm(user_obj, query)
            set_cached_reply(query, reply, sid, user_obj)
        except RuntimeError as e:
            return 503, {"error": f"服务未启用模型：{str(e)}。请在 runserver 或启用相应开关后再试。"}
    logger.info(f"TopKLogSystem的回复：\n{reply}\n")

    # 6. 写入结构化历史并更新会话时间
    History.objects.create(session_id=sid, user=username, user_input=user_input, response=reply)
    session.updated_at = timezone.now()
    session.save(update_fields=["updated_at"])

    return {"reply": reply}


@router.get("/sessions/history", response={200: HistoryListOut, 401: ErrorResponse, 404: ErrorResponse})
def history(request, session_id: str, limit: int = 200, before_id: int | None = None, after_id: int | None = None):
    """结构化获取对话历史：
    - 基于新表 deepseek_api_session / deepseek_api_history
    - 支持分页：before_id/after_id 二选一，limit 默认 200
    """
    if not request.auth:
        return 401, {"error": "未授权"}

    sid = (session_id or "").strip() or "default_session"
    username = request.auth.user

    # 校验会话存在
    if not Session.objects.filter(session_id=sid, user=username).exists():
        return 404, {"error": "会话不存在"}

    # 分页与排序
    qs = History.objects.filter(session_id=sid, user=username)
    if before_id is not None:
        qs = qs.filter(id__lt=before_id).order_by("-id")
    elif after_id is not None:
        qs = qs.filter(id__gt=after_id).order_by("id")
    else:
        qs = qs.order_by("id")

    limit = max(1, min(int(limit or 200), 1000))
    items = list(qs[:limit])

    # 若使用 before_id 且倒序取，需要再翻转为升序返回
    if before_id is not None:
        items = list(reversed(items))

    turns = [
        {"user_input": it.user_input or "", "response": it.response or ""}
        for it in items
    ]

    return {"turns": turns}


@router.delete("/sessions/history", response={200: dict, 401: ErrorResponse, 404: ErrorResponse})
def clear_history(request, session_id: str = "default_session"):
    """清空结构化历史：仅删除 deepseek_api_history 中该会话的记录，Session 保留。
    当会话不存在时返回 404。"""
    if not request.auth:
        return 401, {"error": "未授权"}

    sid = (session_id or "").strip() or "default_session"
    username = request.auth.user

    if not Session.objects.filter(session_id=sid, user=username).exists():
        return 404, {"error": "会话不存在"}

    History.objects.filter(session_id=sid, user=username).delete()
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
    return {"transformers": models.get("transformers", []),
            "ollama": models.get("ollama", [])}


# ----- 会话管理 -----
@router.post("/sessions", response={201: SessionOut, 400: ErrorResponse, 401: ErrorResponse, 409: ErrorResponse})
def create_session(request, data: SessionIn):
    """显式创建新会话，若已存在则返回 409。"""
    if not request.auth:
        return 401, {"error": "未授权"}
    session_id = (data.session_id or "").strip()
    if not session_id:
        return 400, {"error": "session_id 不能为空"}
    username = request.auth.user  # 使用用户名
    # 判断是否已存在
    if Session.objects.filter(session_id=session_id, user=username).exists():
        return 409, {"error": "会话已存在"}
    Session.objects.create(session_id=session_id, user=username)
    return 201, {"session_id": session_id}


@router.delete("/sessions", response={200: dict, 400: ErrorResponse, 401: ErrorResponse, 404: ErrorResponse})
def delete_session(request, data: SessionIn):
    """显式删除会话。如果不存在返回 404。"""
    if not request.auth:
        return 401, {"error": "未授权"}
    session_id = (data.session_id or "").strip()
    if not session_id:
        return 400, {"error": "session_id 不能为空"}
    username = request.auth.user
    qs = Session.objects.filter(session_id=session_id, user=username)
    if not qs.exists():
        return 404, {"error": "会话不存在"}
    qs.delete()
    return {"message": "会话已删除"}


@router.get("/sessions", response={200: SessionListOut, 401: ErrorResponse})
def list_sessions(request):
    """根据 username 列出该用户的全部会话 ID，按最近更新时间倒序（读取 deepseek_api_session）。"""
    if not request.auth:
        return 401, {"error": "未授权"}
    username = request.auth.user
    session_ids = list(
        Session.objects.filter(user=username)
        .order_by("-updated_at")
        .values_list("session_id", flat=True)
    )
    return {"sessions": session_ids}


@router.get("/llm/my", response={200: SelectLLMOut, 401: ErrorResponse})
def get_my_llm(request):
    """返回当前用户选择的 LLM 配置（通过 Bearer token 识别用户）。"""
    if not request.auth:
        return 401, {"error": "未授权"}
    pref = services.get_or_create_user_pref(request.auth)
    return {"provider": pref.provider, "model": pref.model or None}

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


# ===== External API management =====
@router.post("/llm/extern", response={200: dict, 400: ErrorResponse, 401: ErrorResponse})
def add_external_api(request, data: APIIn):
    """添加/更新用户自定义的 OpenAI 兼容接口配置。先校验可用性，再保存。"""
    if not request.auth:
        return 401, {"error": "未授权"}
    base_url = (data.base_url or "").strip()
    model_name = (data.model_name or "").strip()
    api_key = (data.api_key or "").strip()
    alias = (data.alias or None)
    if not base_url or not model_name or not api_key:
        return 400, {"error": "base_url、model_name、api_key 不能为空"}

    # quick validation
    ok = _validate_openai_compat(base_url, api_key, model_name)
    if not ok:
        return 400, {"error": "无法连接到该接口或模型不可用"}

    username = request.auth.user
    obj, created = ExternalLLMAPI.objects.update_or_create(
        user=username, model_name=model_name,
        defaults={"base_url": base_url, "api_key": api_key, "alias": alias},
    )
    return {"message": "保存成功"}


@router.get("/llm/extern", response={200: ModelsListOut, 401: ErrorResponse})
def list_external_models(request):
    if not request.auth:
        return 401, {"error": "未授权"}
    username = request.auth.user
    items = ExternalLLMAPI.objects.filter(user=username).order_by("-updated_at")
    names = [(item.alias or item.model_name).strip() for item in items]
    return {"models_list": names}


@router.delete("/llm/extern", response={200: dict, 400: ErrorResponse, 401: ErrorResponse, 404: ErrorResponse})
def delete_external_model(request, data: ModelIn):
    if not request.auth:
        return 401, {"error": "未授权"}
    key = (data.model_name or "").strip()
    if not key:
        return 400, {"error": "model_name 不能为空"}
    username = request.auth.user
    qs = ExternalLLMAPI.objects.filter(user=username).filter(Q(model_name=key) | Q(alias=key))
    if not qs.exists():
        return 404, {"error": "未找到该模型配置"}
    qs.delete()
    return {"message": "已删除"}


@api.post("/refresh", response={200: dict, 400: ErrorResponse, 403: ErrorResponse})
def refresh(request):
    token = (request.COOKIES.get("refresh_token") or "").strip()
    if not token:
        return 400, {"error": "refresh_token 不能为空"}

    api_key = services.refresh_access_token(token)
    if not api_key:
        return 403, {"error": "refresh_token 无效或已过期"}

    payload = {
        "message": "刷新成功"
    }
    response = api.create_response(request, payload, status=200)
    # 在响应头设置新的 Authorization，便于前端拿到新的 access token
    response["Authorization"] = f"Bearer {api_key.key}"
    # 将现有 refresh_token 继续写入 HttpOnly Cookie（不更换，且不延长有效期）
    response.set_cookie(
        key="refresh_token",
        value=api_key.refresh_token or "",
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
        max_age=getattr(settings, "REFRESH_TOKEN_EXPIRY_SECONDS", None),
        path="/",
    )
    return response

# 将路由添加到API
api.add_router("", router)
