# 数据分析后端（Django）README（简版）

本项目提供“系统日志检索 + 向量检索 + 大语言模型分析”的一体化后端服务，支持多种推理与嵌入后端（本地 Transformers、Ollama、OpenAI 兼容、DashScope 等），并可独立选择对话模型与向量嵌入模型，灵活混搭。

## 1. 功能概览

- 日志数据索引与检索：使用向量数据库（Chroma）对日志内容建索引并进行 Top‑K 召回。
- LLM 分析与答复：将检索到的日志上下文与用户问题交由 LLM 生成结构化答复，且支持输出清洗与模板化。
- 多 Provider 支持：
  - 本地/离线：Transformers（HF）、Ollama
  - 云端/在线：OpenAI 兼容端点、阿里云 DashScope
- 配置即插即用：LLM/Embedding 使用 `llm_config.yaml`，数据库使用独立的 `db_config.yaml`。
- Django API：以 Django + django-ninja 提供简洁的后端接口与管理命令（如 initdb）。

## 2. 关键目录与文件

- manage.py：Django 入口
- deepseek_project/：Django 配置（settings/urls/asgi/wsgi）
- deepseek_api/：后端应用（API、Models、Services、管理命令等）
  - management/commands/initdb.py：初始化示例数据或表结构的管理命令
- topklogsystem.py：日志检索 + LLM 生成的脚本式入口（便于本地快速验证链路）
- config/
  - llm_config.yaml.example：被 Git 跟踪的 LLM 规范配置；本地 `llm_config.yaml` 可由它复制并覆盖
  - db_config.yaml.example：被 Git 跟踪的数据库规范配置；本地 `db_config.yaml` 可由它复制并覆盖
  - system_prompt.yaml：系统提示词模板（含 {log_context}/{query}/{MAX_PARTS_NUM}/{MAX_PART_LENGTH} 占位符）
  - response_template.md：答案渲染模板
  - available_local_models.json / generate_local_model.py：本地模型辅助脚本
- data/
  - log/：示例日志数据
  - vector_stores/：向量索引持久化目录（如 chroma.sqlite3）
- api_key.env：本地开发用的 API Key 环境变量文件（可用系统环境变量替代）
- pyproject.toml：Python 依赖与项目信息（PEP 621）
- uv.lock：依赖锁定文件（建议使用 uv 同步）

## 3. 配置说明

### 3.1 LLM 配置

- 首次克隆时可执行 `cp config/llm_config.yaml.example config/llm_config.yaml`；如果不创建本地文件，程序会直接使用被跟踪的 `.example` 配置。
- `llm_config.yaml.example` 是完整、可审查的配置契约；实际密钥、代理和机器相关路径只写入被 Git 忽略的 `llm_config.yaml`。
- 生成 available_local_models.json（用于枚举本地可用的 HF/Ollama 模型等）
  - 命令：uv run python config/generate_local_model.py
  - 作用：在 config/ 目录下生成/刷新 available_local_models.json，供配置与选择参考。

- LLM_PROVIDER：对话模型提供方（transformers | ollama | openai_compat | dashscope）
- EMBEDDING_PROVIDER：向量嵌入提供方（auto | hf | ollama | openai_compat | dashscope）
  - auto 表示与 LLM_PROVIDER 保持一致
- TRANSFORMERS_CONFIG：本地 HF 生成/嵌入模型与推理参数
- OLLAMA_CONFIG：Ollama 生成与嵌入模型、主机端口等
- OPENAI_COMPAT_CONFIG：OpenAI 或兼容端点（base_url、api_key、模型、重试/超时等）
- DASHSCOPE_CONFIG：DashScope 兼容模式（chat/embedding 模型、维度、超时等）
- LLM_MAX_PARTS_NUM / LLM_MAX_PART_LENGTH：生成结果清洗的段落和单条长度限制（会渲染到 system_prompt.yaml）
- 代理：HTTP_PROXY/HTTPS_PROXY 可选

提示：如更换 EMBEDDING_PROVIDER 或 embedding_model/embedding_dimensions，须删除 data/vector_stores 后重建索引，避免维度不匹配。

### 3.2 数据库配置（config/db_config.yaml）

- 首次使用可执行 `cp config/db_config.yaml.example config/db_config.yaml`；未创建本地文件时回退到跟踪的 `.example` 配置。
- 配置根节点为 `DATABASE`，`ENGINE` 支持 `sqlite`、`mysql` 和 `postgresql`（`postgres` 为别名）。
- SQLite 的 `NAME` 是相对于后端项目根目录的数据库文件路径；MySQL/PostgreSQL 的 `NAME`、`USER`、`PASSWORD`、`HOST`、`PORT` 会直接映射到 Django。
- `DJANGO_DB_CONFIG` 可指定另一份数据库 YAML；SQLite 仍兼容 `DJANGO_DB_PATH` 作为路径覆盖。
- 配置值支持 `${ENV_VAR}`，密码建议通过环境变量注入，不要写入 Git。
- MySQL 使用 `mysqlclient`，PostgreSQL 使用 `psycopg[binary]`；两者已纳入 `pyproject.toml` 与 `uv.lock`。

### 3.3 限流配置

- 限流计数保存在数据库 `RateLimitBucket` 表中，按接口范围、用户/IP 和固定时间窗口计数，适用于 PostgreSQL/MySQL 多 worker 部署。
- 可通过 `RATE_LIMIT_LOGIN_MAX`、`RATE_LIMIT_CHAT_MAX`、`RATE_LIMIT_MODEL_VALIDATE_MAX` 及对应的 `_INTERVAL` 配置登录、聊天和模型验证限额；`RATE_LIMIT_MAX`/`RATE_LIMIT_INTERVAL` 是通用默认值。
- 超额请求返回 HTTP 429、稳定错误码 `RATE_LIMITED` 和 `Retry-After`。默认不信任 `X-Forwarded-For`，反向代理部署时需明确设置 `RATE_LIMIT_TRUST_PROXY=true`。

### 3.4 外部模型配置

- 用户自定义模型通过 `/api/llm/extern` 管理；选择时可使用模型别名或模型名，后端只在当前用户范围内解析，并将偏好绑定到稳定配置 ID。
- 外部 API Key 使用 Fernet 加密保存于 `ExternalLLMAPI.api_key_encrypted`，不出现在 API 响应和日志中。优先设置不入库的 `EXTERNAL_API_ENCRYPTION_KEY`；未设置时由稳定的 `DJANGO_SECRET_KEY` 派生。
- 更新相同模型名会重新加密 API Key；删除当前使用的外部模型时，用户偏好自动回退到 `llm_config.yaml` 的默认模型。Base URL 的协议、内网地址和重定向校验仍属于后续 SSRF 防护任务。

## 4. 环境准备

- Python：建议 3.13（与 pyproject.toml 对齐）
- 可选 GPU：如使用本地 Transformers 推理，建议安装匹配 CUDA 的 PyTorch
- 建议包管理器：uv（已提供 uv.lock）
- 若连接 MySQL/PostgreSQL，请执行 `uv sync` 安装对应驱动，并确认数据库用户已创建且具备迁移权限。
- 本地密钥：在项目根目录创建并填写 api_key.env，或在系统环境中设置：
  - OPENAI_API_KEY / OPENAI_BASE_URL（如使用 OpenAI 兼容端点）
  - DASHSCOPE_API_KEY / DASHSCOPE_BASE_URL（如使用 DashScope）
  - HUGGING_FACE_HUB_TOKEN（如使用受限 HF 模型）

## 5. 使用包管理器安装依赖（基于 pyproject.toml）

首选：uv（快速、跨平台、原生支持 PEP 621）

- 安装 uv：
  - pip install --upgrade uv
- 创建并使用虚拟环境：
  - uv venv .venv
  - Windows：.venv\Scripts\activate
  - macOS/Linux：source .venv/bin/activate
- 同步依赖（读取 pyproject.toml，优先使用 uv.lock）：
  - 使用锁定版本：uv sync --frozen
  - 首次或需更新锁：uv sync

备选 1：pip（若不使用 uv）

- 创建虚拟环境并激活：
  - python -m venv .venv
  - Windows：.venv\Scripts\activate
  - macOS/Linux：source .venv/bin/activate
- 升级 pip：python -m pip install -U pip
- 安装依赖（两种方式，任选其一）：
  - 使用已给出的 requirements.txt：pip install -r requirements.txt
  - 直接依据 pyproject（部分环境需构建后端，可能不如 uv 稳定）：pip install .

备选 2：Poetry（如你偏好，但本仓库未附带 poetry.lock，需自行管理）

- poetry install

## 6. 运行与常用命令

方式 A：启动 Django API 服务

- 数据迁移：
  - uv run python manage.py migrate （或激活虚拟环境后直接 python manage.py migrate）
  - 切换数据库后无需新增专用迁移；Django 会在目标数据库上按既有 0001–0010 顺序执行迁移。生产切换前请备份并在目标数据库副本演练。
  - 0007 迁移会把旧 History 绑定到 Session，并删除无法匹配所属 Session 的孤立记录；0008 会把 Session 用户名迁移为 Django User 外键，并清理无法匹配用户的 Session；生产迁移前请先备份数据库。
- 可选：初始化命令（如有需要）
  - uv run python manage.py initdb
    - 仅迁移不写入种子：uv run python manage.py initdb --no-seed
    - 仅 SQLite 可在 ORM 种子后尝试执行原始 SQL：uv run python manage.py initdb --use-sql --sql init.sql；MySQL/PostgreSQL 请不要使用 `--use-sql`
- 启动开发服务器：
  - uv run python manage.py runserver 0.0.0.0:8000
- Token 生命周期：Access Token 默认 15 分钟；Refresh Token 使用 HttpOnly Cookie 并在刷新时轮换。`ACCESS_TOKEN_EXPIRY_SECONDS`、`REFRESH_TOKEN_EXPIRY_SECONDS`、`AUTH_COOKIE_SECURE` 和 `AUTH_COOKIE_SAMESITE` 可按环境配置。
- 退出登录：前端调用 `POST /api/logout`，服务端撤销当前 Access/Refresh Token 家族并清理 Refresh Cookie。

方式 B：脚本方式快速验证链路（检索 + 生成）

- uv run python topklogsystem.py
- 或在代码中：from topklogsystem import TopKLogSystem; TopKLogSystem.query("你的问题")

### 6.1 自动化测试

测试不会加载真实模型或访问外部网络；Django 测试运行器使用独立测试数据库，模型调用通过 Mock 替代。

```bash
cd backend/django_backend
DJANGO_TESTING=true uv run --project . python manage.py test --noinput
DJANGO_TESTING=true uv run --project . coverage run --data-file=/tmp/data-analyze.coverage --source=deepseek_api,deepseek_project --omit='*/migrations/*,*/tests/*,*/asgi.py,*/wsgi.py' manage.py test --noinput
uv run --project . coverage report --data-file=/tmp/data-analyze.coverage --omit='*/migrations/*,*/tests/*,*/asgi.py,*/wsgi.py' --show-missing
```

当前测试覆盖配置校验、Token、历史选择、缓存、注册登录、会话隔离、Chat 缓存和模型失败路径。

### 6.3 回复缓存正确性

- `REPLY_CACHE_TTL` 控制最终回答缓存时长，默认 3600 秒，设置为 `0` 可关闭缓存写入。
- 缓存键使用 SHA-256，并绑定用户、Session、完整 Prompt、选中的历史、生成参数、Provider/模型/endpoint、Prompt 版本、索引版本和缓存命名空间。
- 更新知识库或 Prompt 后执行 `DJANGO_TESTING=true uv run --project . python manage.py invalidate_reply_cache`，通过轮换命名空间使旧回复立即逻辑失效。
- 空回复、非字符串值和显式标记为不可缓存的错误响应不会写入正常回复缓存。

### 6.4 错误响应契约

- 错误响应统一返回 `code` 和兼容字段 `error`；请求 Schema 校验错误额外返回 `details`。
- 前端应根据 `code` 判断处理方式：`AUTH_REQUIRED/AUTH_INVALID` 重新登录，`VALIDATION_ERROR` 修正输入，`MODEL_UNAVAILABLE` 稍后重试或切换模型，`RATE_LIMITED` 遵守 `Retry-After`。
- 未知异常统一返回 `500/INTERNAL_ERROR`，服务端堆栈只记录在日志中。

### 6.2 Session/History 数据契约

- `History` 通过数据库 ForeignKey 属于 `Session`，删除 Session 会级联删除 History。
- `Session.user` 通过 Django User 外键归属用户；同一 Session 的 Chat 写入使用行锁、单调序号和可选 `message_id` 幂等。
- `GET /api/sessions/history` 返回每条记录的 `id`、`created_at`、正文和 `before_cursor`/`after_cursor` 复合分页游标；旧 ID 参数仍兼容。
- `POST /api/sessions` 支持可选 `title`；不传时默认使用 `session_id`。

## 7. 常见问题

- 更换嵌入模型后报错或召回异常：删除 data/vector_stores 并重建索引。
- DashScope embeddings 兼容性问题：请将 EMBEDDING_PROVIDER 设为 dashscope，并在 DASHSCOPE_CONFIG 中指定正确的 embedding_model 与维度。
- HF 模型权限错误（401 或 gated repo）：更换为公开模型，或配置 HUGGING_FACE_HUB_TOKEN 并确保账号有访问权限。
- 代理：在 llm_config.yaml 配置 HTTP_PROXY/HTTPS_PROXY，程序会在启动时注入环境变量。

## 8. 许可

仅用于教学与研究示例。请在相应平台遵循模型与数据集的使用条款。
