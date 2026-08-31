import base64
import binascii
import json
import logging
import uuid
from datetime import UTC, datetime

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import F, Q
from django.http import Http404
from django.utils import timezone
from ninja import NinjaAPI, Router
from ninja.errors import (
    AuthenticationError,
    AuthorizationError,
    HttpError,
    Throttled,
    ValidationError,
)

from . import services
from .errors import ErrorCode, error_payload
from .models import APIKey, ExternalLLMAPI, History, Session
from .schemas import (
    APIIn,
    ChatIn,
    ChatOut,
    ErrorResponse,
    HistoryListOut,
    LocalModelsOut,
    LoginIn,
    ModelIn,
    ModelsListOut,
    ProvidersOut,
    SelectLLMIn,
    SelectLLMOut,
    SessionIn,
    SessionListOut,
    SessionOut,
)
from .services import (
    compose_prompt_with_history,
    generate_with_user_llm,
    get_allowed_providers,
    get_cached_reply,
    get_history_cfg,
    get_local_models,
    select_history_by_similarity,
    set_cached_reply,
    set_user_pref,
)

logger = logging.getLogger(__name__)

api = NinjaAPI(title="DeepSeek-KAI API", version="0.1.0")


@api.exception_handler(AuthenticationError)
def handle_authentication_error(request, exc):
    if str(exc) == "Unauthorized":
        return api.create_response(
            request,
            error_payload(ErrorCode.AUTH_REQUIRED, "请先登录获取 API Key"),
            status=401,
        )
    return api.create_response(
        request,
        error_payload(ErrorCode.AUTH_INVALID, str(exc)),
        status=exc.status_code,
    )


@api.exception_handler(AuthorizationError)
def handle_authorization_error(request, exc):
    return api.create_response(
        request,
        error_payload(ErrorCode.AUTH_FORBIDDEN, "无权执行该操作"),
        status=exc.status_code,
    )


@api.exception_handler(ValidationError)
def handle_validation_error(request, exc):
    return api.create_response(
        request,
        error_payload(
            ErrorCode.VALIDATION_ERROR,
            "请求参数校验失败",
            details=exc.errors,
        ),
        status=400,
    )


@api.exception_handler(Throttled)
def handle_throttled_error(request, exc):
    response = api.create_response(
        request,
        error_payload(ErrorCode.RATE_LIMITED, "请求过于频繁，请稍后再试"),
        status=429,
    )
    if exc.wait is not None:
        response["Retry-After"] = str(exc.wait)
    return response


@api.exception_handler(Http404)
def handle_not_found_error(request, exc):
    return api.create_response(
        request,
        error_payload(ErrorCode.RESOURCE_NOT_FOUND, "资源不存在"),
        status=404,
    )


@api.exception_handler(HttpError)
def handle_http_error(request, exc):
    code = {
        400: ErrorCode.VALIDATION_ERROR,
        401: ErrorCode.AUTH_REQUIRED,
        403: ErrorCode.AUTH_FORBIDDEN,
        404: ErrorCode.RESOURCE_NOT_FOUND,
        409: ErrorCode.RESOURCE_CONFLICT,
        429: ErrorCode.RATE_LIMITED,
        503: ErrorCode.MODEL_UNAVAILABLE,
    }.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    return api.create_response(request, error_payload(code, str(exc)), status=exc.status_code)


@api.exception_handler(Exception)
def handle_unexpected_error(request, exc):
    logger.exception("Unhandled API error")
    return api.create_response(
        request,
        error_payload(ErrorCode.INTERNAL_ERROR, "服务内部错误，请稍后再试"),
        status=500,
    )


def _get_authenticated_user(request):
    """Resolve the legacy token username to the canonical Django User."""
    if not request.auth:
        return None
    return User.objects.filter(username=request.auth.user).first()


def _encode_history_cursor(item: History) -> str:
    payload = {"created_at": item.created_at.isoformat(), "id": item.id}
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _decode_history_cursor(value: str) -> tuple[datetime, int] | None:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        created_at = datetime.fromisoformat(payload["created_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        history_id = int(payload["id"])
        if history_id <= 0:
            return None
        return created_at, history_id
    except (TypeError, ValueError, KeyError, json.JSONDecodeError, binascii.Error):
        return None


def _get_locked_session(user: User, session_id: str) -> Session:
    """Get or create a session, then lock it for serialized writes."""
    session, _ = Session.objects.get_or_create(
        session_id=session_id,
        user=user,
        defaults={"title": session_id[:200]},
    )
    return Session.objects.select_for_update().get(pk=session.pk)


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
            raise AuthenticationError(message="API Key 无效")
        api_key = APIKey.objects.get(key=key)
        # 过期校验
        if api_key.revoked_at is not None:
            raise AuthenticationError(message="API Key 已撤销")
        if not api_key.is_valid():
            raise AuthenticationError(message="API Key 已过期")
        return api_key
    except (ValueError, APIKey.DoesNotExist):
        raise AuthenticationError(message="API Key 无效") from None


router = Router(auth=api_key_auth)


@api.post(
    "/users/register",
    response={200: dict, 400: ErrorResponse, 409: ErrorResponse, 500: ErrorResponse},
)
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
        return 400, error_payload(ErrorCode.VALIDATION_ERROR, "用户名和密码不能为空")

    if User.objects.filter(username=username).exists():
        return 409, error_payload(ErrorCode.RESOURCE_CONFLICT, "用户名已存在")

    # 创建用户（自动进行密码哈希）
    User.objects.create_user(username=username, password=password)

    # 成功返回 LoginIn 结构体，password 置空
    return {"message": "注册成功"}


@api.post(
    "/users/login",
    response={200: dict, 400: ErrorResponse, 403: ErrorResponse, 500: ErrorResponse},
)
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
        return 400, error_payload(ErrorCode.VALIDATION_ERROR, "用户名和密码不能为空")

    user = authenticate(request, username=username, password=password)
    if user is None:
        if User.objects.filter(username=username, is_active=False).exists():
            return 403, error_payload(ErrorCode.AUTH_FORBIDDEN, "账号已被禁用")
        return 403, error_payload(ErrorCode.AUTH_INVALID, "用户名或密码错误")
    if not getattr(user, "is_active", True):
        return 403, error_payload(ErrorCode.AUTH_FORBIDDEN, "账号已被禁用")

    api_key_obj = services.create_api_key(username)
    payload = {
        "message": "登录成功",
    }
    response = api.create_response(request, payload, status=200)
    # 将 access token 放在响应头（便于前端从 header 读取）
    response["Authorization"] = f"Bearer {api_key_obj.key}"
    # 将 refresh_token 写入 HttpOnly Cookie
    response.set_cookie(
        key=settings.AUTH_REFRESH_COOKIE_NAME,
        value=api_key_obj.refresh_token or "",
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        max_age=getattr(settings, "REFRESH_TOKEN_EXPIRY_SECONDS", None),
        path="/",
    )
    return response


@router.post(
    "/llm/chat",
    response={
        200: ChatOut,
        400: ErrorResponse,
        401: ErrorResponse,
        429: ErrorResponse,
        500: ErrorResponse,
        503: ErrorResponse,
    },
)
def chat(request, data: ChatIn):
    # 1. 认证验证（确保用户已登录）
    if not request.auth:
        return 401, error_payload(ErrorCode.AUTH_REQUIRED, "请先登录获取 API Key")

    # 2. 解析参数（确保 session_id 有效）
    sid = (data.session_id or "").strip() or "default_session"
    user_input = (data.user_input or "").strip()
    if not user_input:
        return 400, error_payload(ErrorCode.VALIDATION_ERROR, "请输入消息内容")
    user = _get_authenticated_user(request)
    if user is None:
        return 401, error_payload(ErrorCode.AUTH_INVALID, "认证用户不存在")

    # 将会话创建、历史读取、模型调用和首条历史写入放在一个事务中。
    # 同一 Session 的请求通过行锁串行化，模型失败时空 Session 也会回滚。
    with transaction.atomic():
        session = _get_locked_session(user, sid)
        message_id = data.message_id or uuid.uuid4()
        previous = History.objects.filter(session=session, message_id=message_id).first()
        if previous is not None:
            return {"reply": previous.response or ""}

        # 4. 构造历史上下文（通过 ForeignKey 约束到当前 Session）
        hist_cfg = get_history_cfg()
        use_history_mode = (data.use_history or hist_cfg.get("mode") or "auto").lower()
        qs = History.objects.filter(session=session).order_by("sequence")
        turns_all = [(h.user_input or "", h.response or "") for h in qs]
        if use_history_mode == "on":
            selected = turns_all[-int(hist_cfg.get("max_turns", 8)) :]
        elif use_history_mode == "auto":
            selected = select_history_by_similarity(user_input, turns_all, hist_cfg)
        else:
            selected = []
        query = compose_prompt_with_history(selected, user_input, hist_cfg)
        logger.info(f"传递给TopKLogSystem的query（含历史{len(selected)}段）：{query}")
        cache_parameters = {
            "history_mode": use_history_mode,
            "history_max_turns": hist_cfg.get("max_turns", 8),
            "history_top_k": hist_cfg.get("top_k", 3),
            "history_sim_threshold": hist_cfg.get("sim_threshold", 0.25),
            "history_max_tokens": hist_cfg.get("max_tokens", 1000),
        }

        # 5. 调用大模型（带缓存）。
        user_obj = request.auth
        user_pref = services.get_or_create_user_pref(user_obj)
        cached_reply = get_cached_reply(
            query,
            sid,
            user_obj,
            provider=user_pref.provider,
            model=user_pref.model or None,
            parameters=cache_parameters,
            history=selected,
        )
        if cached_reply:
            reply = cached_reply
        else:
            try:
                reply = generate_with_user_llm(user_obj, query)
                set_cached_reply(
                    query,
                    reply,
                    sid,
                    user_obj,
                    provider=user_pref.provider,
                    model=user_pref.model or None,
                    parameters=cache_parameters,
                    history=selected,
                )
            except RuntimeError as e:
                transaction.set_rollback(True)
                return 503, error_payload(
                    ErrorCode.MODEL_UNAVAILABLE,
                    f"服务未启用模型：{str(e)}。请在 runserver 或启用相应开关后再试。",
                )
        logger.info(f"TopKLogSystem的回复：\n{reply}\n")

        # 6. 写入结构化历史并更新会话时间
        session.next_history_sequence = F("next_history_sequence") + 1
        session.save(update_fields=["next_history_sequence"])
        session.refresh_from_db(fields=["next_history_sequence"])
        History.objects.create(
            session=session,
            sequence=session.next_history_sequence,
            message_id=message_id,
            user_input=user_input,
            response=reply,
        )
        session.updated_at = timezone.now()
        session.save(update_fields=["updated_at"])

    return {"reply": reply}


@router.get(
    "/sessions/history",
    response={
        200: HistoryListOut,
        400: ErrorResponse,
        401: ErrorResponse,
        404: ErrorResponse,
        429: ErrorResponse,
        500: ErrorResponse,
    },
)
def history(
    request,
    session_id: str,
    limit: int = 200,
    before_cursor: str | None = None,
    after_cursor: str | None = None,
    before_id: int | None = None,
    after_id: int | None = None,
):
    """结构化获取对话历史：
    - 基于新表 deepseek_api_session / deepseek_api_history
    - 支持分页：before_id/after_id 二选一，limit 默认 200
    """
    if not request.auth:
        return 401, error_payload(ErrorCode.AUTH_REQUIRED, "请先登录获取 API Key")

    if before_cursor and before_id is not None:
        return 400, error_payload(
            ErrorCode.VALIDATION_ERROR, "before_cursor 和 before_id 不能同时使用"
        )
    if after_cursor and after_id is not None:
        return 400, error_payload(
            ErrorCode.VALIDATION_ERROR, "after_cursor 和 after_id 不能同时使用"
        )
    if (before_cursor or before_id is not None) and (after_cursor or after_id is not None):
        return 400, error_payload(ErrorCode.VALIDATION_ERROR, "before 和 after 游标不能同时使用")

    sid = (session_id or "").strip() or "default_session"
    user = _get_authenticated_user(request)
    if user is None:
        return 401, error_payload(ErrorCode.AUTH_INVALID, "认证用户不存在")

    # 校验会话存在
    session = Session.objects.filter(session_id=sid, user=user).first()
    if session is None:
        return 404, error_payload(ErrorCode.RESOURCE_NOT_FOUND, "会话不存在")

    # 分页与排序
    qs = History.objects.filter(session=session)
    if before_cursor:
        cursor = _decode_history_cursor(before_cursor)
        if cursor is None:
            return 400, error_payload(ErrorCode.VALIDATION_ERROR, "before_cursor 无效")
        created_at, history_id = cursor
        qs = qs.filter(
            Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=history_id)
        ).order_by("-created_at", "-id")
    elif after_cursor:
        cursor = _decode_history_cursor(after_cursor)
        if cursor is None:
            return 400, error_payload(ErrorCode.VALIDATION_ERROR, "after_cursor 无效")
        created_at, history_id = cursor
        qs = qs.filter(
            Q(created_at__gt=created_at) | Q(created_at=created_at, id__gt=history_id)
        ).order_by("created_at", "id")
    elif before_id is not None:
        qs = qs.filter(id__lt=before_id).order_by("-created_at", "-id")
    elif after_id is not None:
        qs = qs.filter(id__gt=after_id).order_by("created_at", "id")
    else:
        qs = qs.order_by("created_at", "id")

    limit = max(1, min(int(limit or 200), 1000))
    items = list(qs[:limit])

    # 若使用 before_id 且倒序取，需要再翻转为升序返回
    if before_cursor or before_id is not None:
        items = list(reversed(items))

    turns = [
        {
            "id": it.id,
            "sequence": it.sequence,
            "message_id": it.message_id,
            "created_at": it.created_at,
            "user_input": it.user_input or "",
            "response": it.response or "",
        }
        for it in items
    ]
    first_id = items[0].id if items else None
    last_id = items[-1].id if items else None
    first_cursor = _encode_history_cursor(items[0]) if items else None
    last_cursor = _encode_history_cursor(items[-1]) if items else None
    older = (
        Q(created_at__lt=items[0].created_at)
        | Q(created_at=items[0].created_at, id__lt=items[0].id)
        if items
        else Q(pk__in=[])
    )
    newer = (
        Q(created_at__gt=items[-1].created_at)
        | Q(created_at=items[-1].created_at, id__gt=items[-1].id)
        if items
        else Q(pk__in=[])
    )
    return {
        "turns": turns,
        "next_before_id": first_id,
        "next_after_id": last_id,
        "next_before_cursor": first_cursor,
        "next_after_cursor": last_cursor,
        "has_more_before": bool(
            first_id and History.objects.filter(session=session).filter(older).exists()
        ),
        "has_more_after": bool(
            last_id and History.objects.filter(session=session).filter(newer).exists()
        ),
    }


@router.delete(
    "/sessions/history",
    response={
        200: dict,
        401: ErrorResponse,
        404: ErrorResponse,
        429: ErrorResponse,
        500: ErrorResponse,
    },
)
def clear_history(request, session_id: str = "default_session"):
    """清空结构化历史：仅删除 deepseek_api_history 中该会话的记录，Session 保留。
    当会话不存在时返回 404。"""
    if not request.auth:
        return 401, error_payload(ErrorCode.AUTH_REQUIRED, "请先登录获取 API Key")

    sid = (session_id or "").strip() or "default_session"
    user = _get_authenticated_user(request)
    if user is None:
        return 401, error_payload(ErrorCode.AUTH_INVALID, "认证用户不存在")

    session = Session.objects.filter(session_id=sid, user=user).first()
    if session is None:
        return 404, error_payload(ErrorCode.RESOURCE_NOT_FOUND, "会话不存在")

    History.objects.filter(session=session).delete()
    return {"message": "历史记录已清空"}


@router.get(
    "/llm/providers",
    response={200: ProvidersOut, 401: ErrorResponse, 429: ErrorResponse, 500: ErrorResponse},
)
def get_llm_providers(request):
    if not request.auth:
        return 401, error_payload(ErrorCode.AUTH_REQUIRED, "请先登录获取 API Key")
    return {"providers": get_allowed_providers()}


@router.get(
    "/llm/local_models",
    response={200: LocalModelsOut, 401: ErrorResponse, 429: ErrorResponse, 500: ErrorResponse},
)
def get_llm_local_models(request):
    if not request.auth:
        return 401, error_payload(ErrorCode.AUTH_REQUIRED, "请先登录获取 API Key")
    models = get_local_models()
    # 统一使用 transformers/ollama 键名
    return {"transformers": models.get("transformers", []), "ollama": models.get("ollama", [])}


# ----- 会话管理 -----
@router.post(
    "/sessions",
    response={
        201: SessionOut,
        400: ErrorResponse,
        401: ErrorResponse,
        409: ErrorResponse,
        429: ErrorResponse,
        500: ErrorResponse,
    },
)
def create_session(request, data: SessionIn):
    """显式创建新会话，若已存在则返回 409。"""
    if not request.auth:
        return 401, error_payload(ErrorCode.AUTH_REQUIRED, "请先登录获取 API Key")
    session_id = (data.session_id or "").strip()
    if not session_id:
        return 400, error_payload(ErrorCode.VALIDATION_ERROR, "session_id 不能为空")
    user = _get_authenticated_user(request)
    if user is None:
        return 401, error_payload(ErrorCode.AUTH_INVALID, "认证用户不存在")
    # 判断是否已存在
    if Session.objects.filter(session_id=session_id, user=user).exists():
        return 409, error_payload(ErrorCode.RESOURCE_CONFLICT, "会话已存在")
    title = (data.title or "").strip()[:200] or session_id
    Session.objects.create(session_id=session_id, user=user, title=title)
    return 201, {"session_id": session_id, "title": title}


@router.delete(
    "/sessions",
    response={
        200: dict,
        400: ErrorResponse,
        401: ErrorResponse,
        404: ErrorResponse,
        429: ErrorResponse,
        500: ErrorResponse,
    },
)
def delete_session(request, data: SessionIn):
    """显式删除会话。如果不存在返回 404。"""
    if not request.auth:
        return 401, error_payload(ErrorCode.AUTH_REQUIRED, "请先登录获取 API Key")
    session_id = (data.session_id or "").strip()
    if not session_id:
        return 400, error_payload(ErrorCode.VALIDATION_ERROR, "session_id 不能为空")
    user = _get_authenticated_user(request)
    if user is None:
        return 401, error_payload(ErrorCode.AUTH_INVALID, "认证用户不存在")
    qs = Session.objects.filter(session_id=session_id, user=user)
    if not qs.exists():
        return 404, error_payload(ErrorCode.RESOURCE_NOT_FOUND, "会话不存在")
    qs.delete()
    return {"message": "会话已删除"}


@router.get(
    "/sessions",
    response={200: SessionListOut, 401: ErrorResponse, 429: ErrorResponse, 500: ErrorResponse},
)
def list_sessions(request):
    """根据 username 列出该用户的全部会话 ID，按最近更新时间倒序（读取 deepseek_api_session）。"""
    if not request.auth:
        return 401, error_payload(ErrorCode.AUTH_REQUIRED, "请先登录获取 API Key")
    user = _get_authenticated_user(request)
    if user is None:
        return 401, error_payload(ErrorCode.AUTH_INVALID, "认证用户不存在")
    session_ids = list(
        Session.objects.filter(user=user)
        .order_by("-updated_at")
        .values_list("session_id", flat=True)
    )
    return {"sessions": session_ids}


@router.get(
    "/llm/my",
    response={200: SelectLLMOut, 401: ErrorResponse, 429: ErrorResponse, 500: ErrorResponse},
)
def get_my_llm(request):
    """返回当前用户选择的 LLM 配置（通过 Bearer token 识别用户）。"""
    if not request.auth:
        return 401, error_payload(ErrorCode.AUTH_REQUIRED, "请先登录获取 API Key")
    pref = services.get_or_create_user_pref(request.auth)
    return {"provider": pref.provider, "model": pref.model or None}


@router.post(
    "/llm/select",
    response={
        200: SelectLLMOut,
        400: ErrorResponse,
        401: ErrorResponse,
        429: ErrorResponse,
        500: ErrorResponse,
    },
)
def select_llm(request, data: SelectLLMIn):
    if not request.auth:
        return 401, error_payload(ErrorCode.AUTH_REQUIRED, "请先登录获取 API Key")
    allowed = set(get_allowed_providers())
    provider = (data.provider or "").lower()
    if provider not in allowed:
        return 400, error_payload(
            ErrorCode.VALIDATION_ERROR,
            f"不允许的 provider: {provider}. 仅允许: {sorted(allowed)}",
        )
    pref = set_user_pref(request.auth, provider, data.model)
    return {"provider": pref.provider, "model": pref.model or None}


# ===== External API management =====
@router.post(
    "/llm/extern",
    response={
        200: dict,
        400: ErrorResponse,
        401: ErrorResponse,
        429: ErrorResponse,
        500: ErrorResponse,
        503: ErrorResponse,
    },
)
def add_external_api(request, data: APIIn):
    """添加/更新用户自定义的 OpenAI 兼容接口配置。先校验可用性，再保存。"""
    if not request.auth:
        return 401, error_payload(ErrorCode.AUTH_REQUIRED, "请先登录获取 API Key")
    base_url = (data.base_url or "").strip()
    model_name = (data.model_name or "").strip()
    api_key = (data.api_key or "").strip()
    alias = data.alias or None
    if not base_url or not model_name or not api_key:
        return 400, error_payload(
            ErrorCode.VALIDATION_ERROR, "base_url、model_name、api_key 不能为空"
        )

    # quick validation
    ok = _validate_openai_compat(base_url, api_key, model_name)
    if not ok:
        return 503, error_payload(ErrorCode.MODEL_UNAVAILABLE, "无法连接到该接口或模型不可用")

    username = request.auth.user
    obj, created = ExternalLLMAPI.objects.update_or_create(
        user=username,
        model_name=model_name,
        defaults={"base_url": base_url, "api_key": api_key, "alias": alias},
    )
    return {"message": "保存成功"}


@router.get(
    "/llm/extern",
    response={200: ModelsListOut, 401: ErrorResponse, 429: ErrorResponse, 500: ErrorResponse},
)
def list_external_models(request):
    if not request.auth:
        return 401, error_payload(ErrorCode.AUTH_REQUIRED, "请先登录获取 API Key")
    username = request.auth.user
    items = ExternalLLMAPI.objects.filter(user=username).order_by("-updated_at")
    names = [(item.alias or item.model_name).strip() for item in items]
    return {"models_list": names}


@router.delete(
    "/llm/extern",
    response={
        200: dict,
        400: ErrorResponse,
        401: ErrorResponse,
        404: ErrorResponse,
        429: ErrorResponse,
        500: ErrorResponse,
    },
)
def delete_external_model(request, data: ModelIn):
    if not request.auth:
        return 401, error_payload(ErrorCode.AUTH_REQUIRED, "请先登录获取 API Key")
    key = (data.model_name or "").strip()
    if not key:
        return 400, error_payload(ErrorCode.VALIDATION_ERROR, "model_name 不能为空")
    username = request.auth.user
    qs = ExternalLLMAPI.objects.filter(user=username).filter(Q(model_name=key) | Q(alias=key))
    if not qs.exists():
        return 404, error_payload(ErrorCode.RESOURCE_NOT_FOUND, "未找到该模型配置")
    qs.delete()
    return {"message": "已删除"}


@api.post(
    "/refresh",
    response={200: dict, 400: ErrorResponse, 403: ErrorResponse, 500: ErrorResponse},
)
def refresh(request):
    token = (request.COOKIES.get(settings.AUTH_REFRESH_COOKIE_NAME) or "").strip()
    if not token:
        response = api.create_response(
            request,
            error_payload(ErrorCode.VALIDATION_ERROR, "refresh_token 不能为空"),
            status=400,
        )
        response.delete_cookie(
            settings.AUTH_REFRESH_COOKIE_NAME,
            path="/",
            samesite=settings.AUTH_COOKIE_SAMESITE,
        )
        return response

    api_key = services.refresh_access_token(token)
    if not api_key:
        response = api.create_response(
            request,
            error_payload(ErrorCode.AUTH_INVALID, "refresh_token 无效或已过期"),
            status=403,
        )
        response.delete_cookie(
            settings.AUTH_REFRESH_COOKIE_NAME,
            path="/",
            samesite=settings.AUTH_COOKIE_SAMESITE,
        )
        return response

    payload = {"message": "刷新成功"}
    response = api.create_response(request, payload, status=200)
    # 在响应头设置新的 Authorization，便于前端拿到新的 access token
    response["Authorization"] = f"Bearer {api_key.key}"
    # Rotate the refresh token while keeping its original absolute expiry.
    response.set_cookie(
        key=settings.AUTH_REFRESH_COOKIE_NAME,
        value=api_key.refresh_token or "",
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        max_age=getattr(settings, "REFRESH_TOKEN_EXPIRY_SECONDS", None),
        path="/",
    )
    return response


@api.post(
    "/logout",
    response={200: dict, 500: ErrorResponse},
)
def logout(request):
    """Revoke the current access token and refresh-token family."""
    authorization = request.headers.get("Authorization", "")
    access_token = None
    try:
        scheme, value = authorization.split()
        if scheme.lower() == "bearer":
            access_token = value
    except ValueError:
        pass
    refresh_token = (request.COOKIES.get(settings.AUTH_REFRESH_COOKIE_NAME) or "").strip()
    services.revoke_tokens(refresh_token=refresh_token, access_token=access_token)
    response = api.create_response(request, {"message": "退出成功"}, status=200)
    response.delete_cookie(
        settings.AUTH_REFRESH_COOKIE_NAME,
        path="/",
        samesite=settings.AUTH_COOKIE_SAMESITE,
    )
    return response


# 将路由添加到API
api.add_router("", router)
