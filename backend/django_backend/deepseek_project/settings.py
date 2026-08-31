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
]

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
AUTH_REFRESH_COOKIE_NAME = os.getenv("AUTH_REFRESH_COOKIE_NAME", "refresh_token")
AUTH_COOKIE_SECURE = parse_bool(os.getenv("AUTH_COOKIE_SECURE"), not DEBUG)
AUTH_COOKIE_SAMESITE = os.getenv("AUTH_COOKIE_SAMESITE", "Lax").capitalize()
if AUTH_COOKIE_SAMESITE not in {"Lax", "Strict", "None"}:
    raise ValueError("AUTH_COOKIE_SAMESITE 必须是 Lax、Strict 或 None")
if AUTH_COOKIE_SAMESITE == "None" and not AUTH_COOKIE_SECURE:
    raise ValueError("AUTH_COOKIE_SAMESITE=None 时必须启用 AUTH_COOKIE_SECURE")
RATE_LIMIT_MAX = 5000  # 每分钟最大请求数
RATE_LIMIT_INTERVAL = 60
CACHE_MAX_SIZE = 200
CACHE_EXPIRY = 300

# LLM/model loading controls
TESTING = parse_bool(os.getenv("DJANGO_TESTING"), False)
# Whether LLM features are enabled at all (controls if the model can be initialized)
ENABLE_LLM = parse_bool(os.getenv("ENABLE_LLM"), not TESTING)
# Whether to preload model and vector index on app startup
PRELOAD_LLM_ON_STARTUP = parse_bool(os.getenv("PRELOAD_LLM_ON_STARTUP"), not TESTING)
