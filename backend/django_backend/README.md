# 数据分析后端（Django）README（简版）

本项目提供“系统日志检索 + 向量检索 + 大语言模型分析”的一体化后端服务，支持多种推理与嵌入后端（本地 Transformers、Ollama、OpenAI 兼容、DashScope 等），并可独立选择对话模型与向量嵌入模型，灵活混搭。


## 1. 功能概览
- 日志数据索引与检索：使用向量数据库（Chroma）对日志内容建索引并进行 Top‑K 召回。
- LLM 分析与答复：将检索到的日志上下文与用户问题交由 LLM 生成结构化答复，且支持输出清洗与模板化。
- 多 Provider 支持：
  - 本地/离线：Transformers（HF）、Ollama
  - 云端/在线：OpenAI 兼容端点、阿里云 DashScope
- 配置即插即用：通过 config/llm_config.yaml 一处集中切换 LLM 与 Embeddings 提供方、模型及参数。
- Django API：以 Django + django-ninja 提供简洁的后端接口与管理命令（如 initdb）。


## 2. 关键目录与文件
- manage.py：Django 入口
- deepseek_project/：Django 配置（settings/urls/asgi/wsgi）
- deepseek_api/：后端应用（API、Models、Services、管理命令等）
  - management/commands/initdb.py：初始化示例数据或表结构的管理命令
- topklogsystem.py：日志检索 + LLM 生成的脚本式入口（便于本地快速验证链路）
- config/
  - llm_config.yaml：核心配置（LLM/Embedding Provider、模型、参数、代理等）
  - system_prompt.yaml：系统提示词模板（含 {log_context}/{query}/{MAX_PARTS_NUM}/{MAX_PART_LENGTH} 占位符）
  - response_template.md：答案渲染模板
  - available_local_models.json / generate_local_model.py / generate_llm_config.py：本地模型与配置辅助脚本
- data/
  - log/：示例日志数据
  - vector_stores/：向量索引持久化目录（如 chroma.sqlite3）
- api_key.env：本地开发用的 API Key 环境变量文件（可用系统环境变量替代）
- pyproject.toml：Python 依赖与项目信息（PEP 621）
- uv.lock：依赖锁定文件（建议使用 uv 同步）


## 3. 配置说明（config/llm_config.yaml）

首次生成配置与本地模型清单：
- 生成 llm_config.yaml（首次克隆仓库或不存在时）
  - 命令：uv run python config/generate_llm_config.py
  - 作用：在 config/ 目录下创建/覆盖 llm_config.yaml，并给出可用 Provider/模型的示例条目，便于后续按需修改。
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


## 4. 环境准备
- Python：建议 3.13（与 pyproject.toml 对齐）
- 可选 GPU：如使用本地 Transformers 推理，建议安装匹配 CUDA 的 PyTorch
- 建议包管理器：uv（已提供 uv.lock）
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
  - uv run python manage.py migrate    （或激活虚拟环境后直接 python manage.py migrate）
- 可选：初始化命令（如有需要）
  - uv run python manage.py initdb
    - 仅迁移不写入种子：uv run python manage.py initdb --no-seed
    - 在 ORM 种子后尝试执行原始 SQL（谨慎使用）：uv run python manage.py initdb --use-sql --sql init.sql
- 启动开发服务器：
  - uv run python manage.py runserver 0.0.0.0:8000

方式 B：脚本方式快速验证链路（检索 + 生成）
- uv run python topklogsystem.py
- 或在代码中：from topklogsystem import TopKLogSystem; TopKLogSystem.query("你的问题")


## 7. 常见问题
- 更换嵌入模型后报错或召回异常：删除 data/vector_stores 并重建索引。
- DashScope embeddings 兼容性问题：请将 EMBEDDING_PROVIDER 设为 dashscope，并在 DASHSCOPE_CONFIG 中指定正确的 embedding_model 与维度。
- HF 模型权限错误（401 或 gated repo）：更换为公开模型，或配置 HUGGING_FACE_HUB_TOKEN 并确保账号有访问权限。
- 代理：在 llm_config.yaml 配置 HTTP_PROXY/HTTPS_PROXY，程序会在启动时注入环境变量。


## 8. 许可
仅用于教学与研究示例。请在相应平台遵循模型与数据集的使用条款。
