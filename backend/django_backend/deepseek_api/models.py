import logging
import secrets
import time
import uuid

from django.conf import settings
from django.db import models
from django.db.models import F

logger = logging.getLogger(__name__)


class APIKey(models.Model):
    key = models.CharField(max_length=64, unique=True)
    user = models.CharField(max_length=100, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expiry_time = models.IntegerField()  # 过期时间戳
    refresh_token = models.CharField(max_length=128, unique=True, null=True, blank=True)
    refresh_expiry_time = models.IntegerField(null=True, blank=True)
    revoked_at = models.IntegerField(null=True, blank=True, db_index=True)

    @classmethod
    def generate_key(cls, length=32):
        """Generate an access token from the operating system CSPRNG."""
        return secrets.token_urlsafe(length)[:length]

    @classmethod
    def generate_refresh_token(cls, length=64):
        """Generate a refresh token from the operating system CSPRNG."""
        return secrets.token_urlsafe(length)

    def is_valid(self):
        """检查 API Key 是否未过期"""
        return self.revoked_at is None and time.time() < self.expiry_time

    def refresh_validity(self, ttl_seconds: int):
        self.expiry_time = int(time.time()) + int(ttl_seconds)
        self.save(update_fields=["expiry_time"])

    def __str__(self):
        token = self.key or ""
        masked = f"{token[:4]}…{token[-4:]}" if len(token) > 8 else "[masked]"
        return f"{self.user} - {masked}"


class RefreshToken(models.Model):
    """One server-side record for each refresh token rotation."""

    api_key = models.ForeignKey(APIKey, on_delete=models.CASCADE, related_name="refresh_tokens")
    token_hash = models.CharField(max_length=64, unique=True)
    family_id = models.UUIDField(default=uuid.uuid4, db_index=True, editable=False)
    issued_at = models.IntegerField()
    expires_at = models.IntegerField()
    used_at = models.IntegerField(null=True, blank=True)
    revoked_at = models.IntegerField(null=True, blank=True)
    replaced_by_hash = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["family_id", "revoked_at"]),
            models.Index(fields=["api_key", "expires_at"]),
        ]

    def __str__(self):
        return f"refresh-family:{self.family_id} api-key:{self.api_key_id}"


class RateLimit(models.Model):
    api_key = models.ForeignKey(
        APIKey, on_delete=models.CASCADE, db_index=True, related_name="rate_limits"
    )
    count = models.IntegerField(default=0)
    reset_time = models.IntegerField()  # 重置时间戳

    class Meta:
        indexes = [models.Index(fields=["api_key", "reset_time"])]

    def should_limit(self, max_requests, interval):
        """检查是否应该限制请求"""
        current_time = time.time()
        if current_time > self.reset_time:
            self.count = 0
            self.reset_time = current_time + interval
            self.save()
            return False
        return self.count >= max_requests


class RateLimitBucket(models.Model):
    """Shared fixed-window counter used by the request-level rate limiter."""

    scope = models.CharField(max_length=64)
    subject = models.CharField(max_length=128)
    window_start = models.BigIntegerField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "subject", "window_start"],
                name="unique_rate_limit_bucket",
            )
        ]
        indexes = [
            models.Index(fields=["scope", "subject", "window_start"]),
            models.Index(fields=["window_start"]),
        ]


class ConversationSession(models.Model):
    session_id = models.CharField(max_length=100)
    # 修改为与 username 关联，而不是 APIKey
    user = models.CharField(max_length=100, db_index=True)
    context = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("session_id", "user")  # 确保用户+会话ID唯一

    def update_context(self, user_input, bot_reply):
        """原子更新上下文，避免并发覆盖"""
        new_entry = f"用户：{user_input}\n回复：{bot_reply}\n"
        # 数据库层面拼接，而非内存中
        ConversationSession.objects.filter(
            pk=self.pk,  # 精确匹配当前会话
            user=self.user,  # 确保用户一致
        ).update(context=F("context") + new_entry)
        # 刷新实例，获取更新后的值
        self.refresh_from_db()

    def clear_context(self):
        """清空对话上下文"""
        self.context = ""
        self.save()

    def __str__(self):
        return self.session_id


class Session(models.Model):
    """当前 API 使用的会话实体。"""

    session_id = models.CharField(max_length=100, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_sessions",
    )
    title = models.CharField(max_length=200, blank=True, default="")
    next_history_sequence = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "deepseek_api_session"
        unique_together = ("session_id", "user")
        indexes = [
            models.Index(
                fields=["user", "updated_at"],
                name="deepseek_api_user_time_idx",
            ),
        ]

    def __str__(self):
        return f"{self.user.username}:{self.session_id}"


class History(models.Model):
    """当前 API 使用的对话轮次，删除所属 Session 时级联删除。"""

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="histories",
    )
    sequence = models.PositiveIntegerField()
    message_id = models.UUIDField(default=uuid.uuid4, editable=False)
    user_input = models.TextField(blank=True, null=True)
    response = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "deepseek_api_history"
        indexes = [
            models.Index(
                fields=["session", "created_at", "id"],
                name="deepseek_api_hist_cursor_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "sequence"],
                name="deepseek_api_hist_seq_uniq",
            ),
            models.UniqueConstraint(
                fields=["session", "message_id"],
                name="deepseek_api_hist_msg_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.session.user}:{self.session.session_id}@{self.pk}"


class UserLLMPreference(models.Model):
    """存储用户选择的 LLM 提供方/模型。未设置时按配置默认插入。"""

    user = models.OneToOneField(APIKey, on_delete=models.CASCADE, related_name="llm_pref")
    provider = models.CharField(max_length=64)  # transformers|ollama|openai_compat|dashscope
    model = models.CharField(max_length=256, blank=True, default="")  # 可选：具体模型名
    external_api = models.ForeignKey(
        "ExternalLLMAPI",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="preferences",
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.user}:{self.provider}:{self.model}"


class ExternalLLMAPI(models.Model):
    """用户自定义的 OpenAI 兼容接口配置。与用户名关联。"""

    user = models.CharField(max_length=100, db_index=True)
    base_url = models.CharField(max_length=512)
    model_name = models.CharField(max_length=128)
    api_key_encrypted = models.CharField(max_length=512)
    alias = models.CharField(max_length=128, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "model_name")
        indexes = [
            models.Index(fields=["user", "model_name"]),
        ]

    def display_name(self) -> str:
        return (self.alias or self.model_name).strip()

    def __str__(self):
        return f"{self.user}:{self.display_name()}"
