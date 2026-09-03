import os
from pathlib import Path

from .configuration import load_database_config, parse_bool, parse_csv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-placeholder-change-me")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = parse_bool(os.getenv("DJANGO_DEBUG"), True)

ALLOWED_HOSTS = parse_csv(os.getenv("DJANGO_ALLOWED_HOSTS"), ["0.0.0.0", "localhost", "127.0.0.1"])

# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "ninja",
    "corsheaders",
    "deepseek_api",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "deepseek_project.middleware.RequestTraceMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "deepseek_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "deepseek_project.wsgi.application"

# Database configuration is kept separate from LLM/application settings.
DATABASES = {"default": load_database_config(project_root=BASE_DIR)}

# Production workers must share answer/retrieval cache entries. Tests opt into
# an isolated in-memory backend so they remain deterministic and offline.
TESTING = parse_bool(os.getenv("DJANGO_TESTING"), False)
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
if TESTING:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "deepseek-tests",
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "KEY_PREFIX": os.getenv("CACHE_KEY_PREFIX", "deepseek"),
        }
    }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# 允许前端域名（根据实际前端地址修改）
CORS_ALLOWED_ORIGINS = parse_csv(
    os.getenv("CORS_ALLOWED_ORIGINS"),
    ["http://localhost:8082", "http://127.0.0.1:8082"],
)

# 允许请求头携带 Authorization
CORS_ALLOW_HEADERS = [
    "authorization",
    "content-type",
]
# 允许跨域携带 Cookie（以使用 HttpOnly Refresh Token）
CORS_ALLOW_CREDENTIALS = True
# 允许前端读取响应头中的 Authorization（便于拿到新的 access token）
CORS_EXPOSE_HEADERS = [
    "Authorization",
    "X-Request-ID",
    "X-Trace-ID",
]

INDEX_STATE_FILE = os.getenv(
    "INDEX_STATE_FILE", str(BASE_DIR / "data" / "vector_stores" / ".index_state.json")
)
try:
    OBSERVABILITY_WORKER_CAPACITY = max(1, int(os.getenv("OBSERVABILITY_WORKER_CAPACITY", "1")))
except ValueError:
    OBSERVABILITY_WORKER_CAPACITY = 1
OBSERVABILITY_PROVIDER_COST_USD_PER_1K = os.getenv("OBSERVABILITY_PROVIDER_COST_USD_PER_1K", "")

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = "static/"

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 自定义配置
API_KEY_LENGTH = 32
TOKEN_EXPIRY_SECONDS = int(os.getenv("ACCESS_TOKEN_EXPIRY_SECONDS", "900"))
REFRESH_TOKEN_EXPIRY_SECONDS = int(os.getenv("REFRESH_TOKEN_EXPIRY_SECONDS", "2592000"))
EXTERNAL_API_ENCRYPTION_KEY = os.getenv("EXTERNAL_API_ENCRYPTION_KEY", "")
ALLOW_INSECURE_EXTERNAL_HTTP = parse_bool(os.getenv("ALLOW_INSECURE_EXTERNAL_HTTP"), False)
EXTERNAL_API_CONNECT_TIMEOUT_SECONDS = float(os.getenv("EXTERNAL_API_CONNECT_TIMEOUT_SECONDS", "5"))
EXTERNAL_API_READ_TIMEOUT_SECONDS = float(os.getenv("EXTERNAL_API_READ_TIMEOUT_SECONDS", "10"))
EXTERNAL_API_WRITE_TIMEOUT_SECONDS = float(os.getenv("EXTERNAL_API_WRITE_TIMEOUT_SECONDS", "5"))
EXTERNAL_API_POOL_TIMEOUT_SECONDS = float(os.getenv("EXTERNAL_API_POOL_TIMEOUT_SECONDS", "5"))
EXTERNAL_API_MAX_RESPONSE_BYTES = int(os.getenv("EXTERNAL_API_MAX_RESPONSE_BYTES", "1048576"))
EXTERNAL_API_MAX_REDIRECTS = int(os.getenv("EXTERNAL_API_MAX_REDIRECTS", "0"))
AUTH_REFRESH_COOKIE_NAME = os.getenv("AUTH_REFRESH_COOKIE_NAME", "refresh_token")
AUTH_COOKIE_SECURE = parse_bool(os.getenv("AUTH_COOKIE_SECURE"), not DEBUG)
AUTH_COOKIE_SAMESITE = os.getenv("AUTH_COOKIE_SAMESITE", "Lax").capitalize()
if AUTH_COOKIE_SAMESITE not in {"Lax", "Strict", "None"}:
    raise ValueError("AUTH_COOKIE_SAMESITE 必须是 Lax、Strict 或 None")
if AUTH_COOKIE_SAMESITE == "None" and not AUTH_COOKIE_SECURE:
    raise ValueError("AUTH_COOKIE_SAMESITE=None 时必须启用 AUTH_COOKIE_SECURE")
RATE_LIMIT_MAX = 5000  # 每分钟最大请求数
RATE_LIMIT_INTERVAL = 60
RATE_LIMIT_LOGIN_MAX = int(os.getenv("RATE_LIMIT_LOGIN_MAX", "10"))
RATE_LIMIT_LOGIN_INTERVAL = int(os.getenv("RATE_LIMIT_LOGIN_INTERVAL", "60"))
RATE_LIMIT_REFRESH_MAX = int(os.getenv("RATE_LIMIT_REFRESH_MAX", "20"))
RATE_LIMIT_REFRESH_INTERVAL = int(os.getenv("RATE_LIMIT_REFRESH_INTERVAL", "60"))
RATE_LIMIT_CHAT_MAX = int(os.getenv("RATE_LIMIT_CHAT_MAX", "60"))
RATE_LIMIT_CHAT_INTERVAL = int(os.getenv("RATE_LIMIT_CHAT_INTERVAL", "60"))
RATE_LIMIT_MODEL_VALIDATE_MAX = int(os.getenv("RATE_LIMIT_MODEL_VALIDATE_MAX", "5"))
RATE_LIMIT_MODEL_VALIDATE_INTERVAL = int(os.getenv("RATE_LIMIT_MODEL_VALIDATE_INTERVAL", "60"))
RATE_LIMIT_API_MAX = int(os.getenv("RATE_LIMIT_API_MAX", str(RATE_LIMIT_MAX)))
RATE_LIMIT_API_INTERVAL = int(os.getenv("RATE_LIMIT_API_INTERVAL", str(RATE_LIMIT_INTERVAL)))
RATE_LIMIT_TRUST_PROXY = parse_bool(os.getenv("RATE_LIMIT_TRUST_PROXY"), False)
CACHE_MAX_SIZE = 200
CACHE_EXPIRY = 300
CACHE_MAX_OBJECT_BYTES = int(os.getenv("CACHE_MAX_OBJECT_BYTES", "262144"))
CACHE_SINGLE_FLIGHT_TIMEOUT = int(os.getenv("CACHE_SINGLE_FLIGHT_TIMEOUT", "120"))

# Persistent structured logging is enabled by default outside tests. Each
# level gets its own JSONL file and is rotated by the logging handler.
PERSISTENT_LOG_ENABLED = parse_bool(os.getenv("PERSISTENT_LOG_ENABLED"), not TESTING)
PERSISTENT_LOG_DIR = Path(
    os.getenv("PERSISTENT_LOG_DIR", str(BASE_DIR / "data" / "log"))
).expanduser()
PERSISTENT_LOG_LEVEL = os.getenv("PERSISTENT_LOG_LEVEL", "INFO").upper()
PERSISTENT_LOG_ROTATION_WHEN = os.getenv("PERSISTENT_LOG_ROTATION_WHEN", "midnight")
try:
    PERSISTENT_LOG_ROTATION_INTERVAL = max(
        1, int(os.getenv("PERSISTENT_LOG_ROTATION_INTERVAL", "1"))
    )
except ValueError:
    PERSISTENT_LOG_ROTATION_INTERVAL = 1
try:
    PERSISTENT_LOG_BACKUP_COUNT = max(0, int(os.getenv("PERSISTENT_LOG_BACKUP_COUNT", "14")))
except ValueError:
    PERSISTENT_LOG_BACKUP_COUNT = 14
PERSISTENT_LOG_UTC = parse_bool(os.getenv("PERSISTENT_LOG_UTC"), True)

# LLM/model loading controls
# Whether LLM features are enabled at all (controls if the model can be initialized)
ENABLE_LLM = parse_bool(os.getenv("ENABLE_LLM"), not TESTING)
# Whether to preload model and vector index on app startup
PRELOAD_LLM_ON_STARTUP = parse_bool(os.getenv("PRELOAD_LLM_ON_STARTUP"), not TESTING)

_log_handlers = ["console_json"]
_log_filters = {
    "request_context": {
        "()": "deepseek_project.observability.RequestContextFilter",
    },
}
_log_handler_config = {
    "console_json": {
        "class": "logging.StreamHandler",
        "filters": ["request_context"],
        "formatter": "json",
        "level": PERSISTENT_LOG_LEVEL,
    },
}
if PERSISTENT_LOG_ENABLED:
    PERSISTENT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    _persistent_levels = {
        "debug": ("DEBUG", "DEBUG"),
        "info": ("INFO", "INFO"),
        "warning": ("WARNING", "WARNING"),
        "error": ("ERROR", "CRITICAL"),
    }
    for name, (min_level, max_level) in _persistent_levels.items():
        _log_filters[f"persistent_{name}_level"] = {
            "()": "deepseek_project.observability.LevelRangeFilter",
            "min_level": min_level,
            "max_level": max_level,
        }
        _log_handler_config[f"persistent_{name}"] = {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "filename": str(PERSISTENT_LOG_DIR / f"{name}.jsonl"),
            "when": PERSISTENT_LOG_ROTATION_WHEN,
            "interval": PERSISTENT_LOG_ROTATION_INTERVAL,
            "backupCount": PERSISTENT_LOG_BACKUP_COUNT,
            "utc": PERSISTENT_LOG_UTC,
            "encoding": "utf-8",
            "delay": True,
            "filters": ["request_context", f"persistent_{name}_level"],
            "formatter": "json",
            "level": min_level,
        }
    _log_handlers.extend(name for name in _log_handler_config if name != "console_json")

# Emit one-line structured logs with request/trace context. The formatter
# intentionally keeps exception messages bounded and redacts credential-like
# fields before they reach the console or a persistent log file.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": _log_filters,
    "formatters": {
        "json": {
            "()": "deepseek_project.observability.JsonFormatter",
        },
    },
    "handlers": _log_handler_config,
    "loggers": {
        "deepseek_api": {
            "handlers": _log_handlers,
            "level": PERSISTENT_LOG_LEVEL,
            "propagate": False,
        },
        "deepseek_project": {
            "handlers": _log_handlers,
            "level": PERSISTENT_LOG_LEVEL,
            "propagate": False,
        },
        "topklogsystem": {
            "handlers": _log_handlers,
            "level": PERSISTENT_LOG_LEVEL,
            "propagate": False,
        },
        "django.request": {
            "handlers": _log_handlers,
            "level": "WARNING",
            "propagate": False,
        },
    },
}
