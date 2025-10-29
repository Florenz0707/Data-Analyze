import logging
import random
import string
import time

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

    @classmethod
    def generate_key(cls, length=32):
        """生成随机 API Key"""
        characters = string.ascii_letters + string.digits
        return ''.join(random.choice(characters) for _ in range(length))

    @classmethod
    def generate_refresh_token(cls, length=64):
        characters = string.ascii_letters + string.digits
        return ''.join(random.choice(characters) for _ in range(length))

    def is_valid(self):
        """检查 API Key 是否未过期"""
        return time.time() < self.expiry_time

    def refresh_validity(self, ttl_seconds: int):
        self.expiry_time = int(time.time()) + int(ttl_seconds)
        self.save(update_fields=["expiry_time"])

    def __str__(self):
        return f"{self.user} - {self.key}"


class RateLimit(models.Model):
    api_key = models.ForeignKey(APIKey, on_delete=models.CASCADE,
                                db_index=True, to_field='key', related_name='rate_limits')
    count = models.IntegerField(default=0)
    reset_time = models.IntegerField()  # 重置时间戳

    class Meta:
        indexes = [
            models.Index(fields=['api_key', 'reset_time'])
        ]

    def should_limit(self, max_requests, interval):
        """检查是否应该限制请求"""
        current_time = time.time()
        if current_time > self.reset_time:
            self.count = 0
            self.reset_time = current_time + interval
            self.save()
            return False
        return self.count >= max_requests


class ConversationSession(models.Model):
    session_id = models.CharField(max_length=100)
    # 修改为与 username 关联，而不是 APIKey
    user = models.CharField(max_length=100, db_index=True)
    context = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('session_id', 'user')  # 确保用户+会话ID唯一

    def update_context(self, user_input, bot_reply):
        """原子更新上下文，避免并发覆盖"""
        new_entry = f"用户：{user_input}\n回复：{bot_reply}\n"
        # 数据库层面拼接，而非内存中
        ConversationSession.objects.filter(
            pk=self.pk,  # 精确匹配当前会话
            user=self.user  # 确保用户一致
        ).update(context=F('context') + new_entry)
        # 刷新实例，获取更新后的值
        self.refresh_from_db()

    def clear_context(self):
        """清空对话上下文"""
        self.context = ""
        self.save()

    def __str__(self):
        return self.session_id


class UserLLMPreference(models.Model):
    """存储用户选择的 LLM 提供方/模型。未设置时按配置默认插入。"""
    user = models.OneToOneField(APIKey, on_delete=models.CASCADE, related_name='llm_pref')
    provider = models.CharField(max_length=64)  # transformers|ollama|openai_compat|dashscope
    model = models.CharField(max_length=256, blank=True, default="")  # 可选：具体模型名
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.user}:{self.provider}:{self.model}"


class ExternalLLMAPI(models.Model):
    """用户自定义的 OpenAI 兼容接口配置。与用户名关联。"""
    user = models.CharField(max_length=100, db_index=True)
    base_url = models.CharField(max_length=512)
    model_name = models.CharField(max_length=128)
    api_key = models.CharField(max_length=256)
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
