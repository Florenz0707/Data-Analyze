# 项目说明与配置指南（适配最新的 config/llm_config.yaml）

本项目提供“日志检索 + LLM 分析”的一体化能力，支持本地/云端多种推理后端，并且允许“对话模型（LLM）”与“向量嵌入模型（Embeddings）独立选择、灵活混搭”。

核心文件：
- 代码入口与业务逻辑：topklogsystem.py
- 全局配置：config/llm_config.yaml
- 系统提示词模板：config/system_prompt.yaml（包含占位符 {log_context}/{query}/{MAX_PARTS_NUM}/{MAX_PART_LENGTH}）
- 回答模板：config/response_template.md
- API Key 文件（本地开发用）：api_key.env（可用系统环境变量替代）

## 一、环境与依赖

基础依赖（关键项）：
- llama-index
- langchain-openai（用于 OpenAI 兼容协议，以及作为 DashScope Chat 的兼容客户端）
- langchain-huggingface
- langchain-ollama
- transformers
- sentence-transformers（Hugging Face Embeddings 常用）
- chromadb
- pandas
- python-dotenv（加载 api_key.env）
- openai（DashScope Embeddings 专用适配使用 openai 官方 SDK）

安装示例（根据你的环境调整）：
- pip install llama-index langchain-openai langchain-huggingface langchain-ollama transformers sentence-transformers chromadb pandas python-dotenv openai

## 二、配置总览：config/llm_config.yaml

关键顶层键：
- HTTP_PROXY / HTTPS_PROXY：可选，若需走代理。
- LLM_PROVIDER：选择对话模型提供方。
  - 可选值：
    - transformers（本地 HF）
    - ollama（本地/远程 Ollama）
    - openai_compat（OpenAI 兼容协议，如 OpenAI/DeepSeek/自建网关）
    - dashscope（阿里云百炼；Chat 走 OpenAI 兼容，Embeddings 走专用适配）
- EMBEDDING_PROVIDER：选择向量嵌入提供方，支持独立于 LLM。
  - 可选值：auto | hf | ollama | openai_compat | dashscope
  - auto 表示“跟随 LLM_PROVIDER”（保持历史行为）。
- LLM_GENERATION_RETRIES / LLM_MIN_OUTPUT_CHARS：生成鲁棒性设置。
- LLM_MAX_PARTS_NUM / LLM_MAX_PART_LENGTH：输出清洗阶段控制每段条数与每条字符数（会渲染到 system_prompt 中的 {MAX_PARTS_NUM}/{MAX_PART_LENGTH}）。
- 路径相关：LOG_PATH、SYSTEM_PROMPT_PATH、RESPONSE_TEMPLATE_PATH。

各 Provider 专属配置块：
- TRANSFORMERS_CONFIG（当 LLM_PROVIDER=transformers 时生效；同时 hf Embeddings 也从此处读取 embedding_model）
  - llm_model：HF 文本生成模型仓库名或本地路径
  - 生成参数：max_new_tokens/temperature/top_p/repetition_penalty/do_sample
  - 设备设置：device/device_map/torch_dtype/trust_remote_code
  - embedding_model：HF Embeddings 模型名（如 sentence-transformers/all-MiniLM-L6-v2、BAAI/bge-base-zh-v1.5）
- OLLAMA_CONFIG（当 LLM_PROVIDER=ollama 或 EMBEDDING_PROVIDER=ollama）
  - model：Ollama LLM 模型名
  - embedding_model：Ollama Embeddings 模型名（如 bge-large:latest）
  - host/port/api_key 等（如你的服务做了鉴权）
- OPENAI_COMPAT_CONFIG（当 LLM_PROVIDER 或 EMBEDDING_PROVIDER 为 openai_compat）
  - base_url：OpenAI 或兼容端点（如 https://api.openai.com/v1 或你的自建网关）
  - api_key_env_name：从环境变量或 api_key.env 中读取的 Key 名（默认 OPENAI_API_KEY）
  - organization/timeout/max_retries
  - model：生成模型（如 gpt-4o-mini）
  - embedding_model：向量模型（如 text-embedding-3-large）
  - embedding_dimensions：可选，部分兼容端点需要显式维度（如 1024/1536/3072）
- DASHSCOPE_CONFIG（当 LLM_PROVIDER 或 EMBEDDING_PROVIDER 为 dashscope）
  - base_url：DashScope 兼容端点（北京/新加坡不同）
  - api_key_env_name：默认 DASHSCOPE_API_KEY
  - timeout/max_retries
  - chat_model：如 qwen-turbo/qwen-plus/qwen-max
  - embedding_model：如 text-embedding-v4
  - embedding_dimensions：如 1024（按官方文档实际设置）

## 三、API Key 管理：api_key.env

- 本地开发可在根目录放置 api_key.env，项目启动会自动加载。
- 支持的变量（常见）：
  - OPENAI_API_KEY / OPENAI_ORG / OPENAI_BASE_URL
  - DASHSCOPE_API_KEY / DASHSCOPE_BASE_URL
- 生产环境建议使用系统环境变量注入，不要将 api_key.env 提交到仓库。

## 四、独立配置示例

1) LLM 用云端（OpenAI 兼容），Embedding 用本地 HF：

```
LLM_PROVIDER: openai_compat
EMBEDDING_PROVIDER: hf

TRANSFORMERS_CONFIG:
  embedding_model: sentence-transformers/all-MiniLM-L6-v2

OPENAI_COMPAT_CONFIG:
  base_url: https://api.openai.com/v1
  api_key_env_name: OPENAI_API_KEY
  model: gpt-4o-mini
```

2) 全部用 DashScope（生成 + 向量）：

```
LLM_PROVIDER: dashscope
EMBEDDING_PROVIDER: dashscope

DASHSCOPE_CONFIG:
  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
  api_key_env_name: DASHSCOPE_API_KEY
  chat_model: qwen-turbo
  embedding_model: text-embedding-v4
  embedding_dimensions: 1024
```

3) 本地 HF 生成 + 本地 HF 向量（纯离线）：

```
LLM_PROVIDER: transformers
EMBEDDING_PROVIDER: hf

TRANSFORMERS_CONFIG:
  llm_model: Qwen/Qwen2.5-1.5B-Instruct
  embedding_model: sentence-transformers/all-MiniLM-L6-v2
```

4) 本地或远程 Ollama 生成 + Ollama 向量：

```
LLM_PROVIDER: ollama
EMBEDDING_PROVIDER: ollama

OLLAMA_CONFIG:
  model: deepseek-r1:7b
  embedding_model: bge-large:latest
  host: http://localhost:11434
  port: 11434
```

注意：更换 EMBEDDING_PROVIDER 或 embedding_model/embedding_dimensions 后，需要删除 ./data/vector_stores 并重建索引，避免维度不匹配。

## 五、运行

- 启动：

```
python topklogsystem.py
```

- 调用：
  - TopKLogSystem.query("你的问题") 将完成“检索 -> 生成 -> 清洗”的整链路。

## 六、常见问题 & 排错

- DashScope embeddings 返回 400（contents is neither str nor list of str）
  - 原因：DashScope 的 “OpenAI 兼容模式” 对 embeddings 的兼容与 OpenAI 有差异。
  - 解决：已内置 dashscope 分支，Embeddings 通过 openai 官方 SDK 直连其 embeddings 接口；请使用 EMBEDDING_PROVIDER: dashscope 并配置 DASHSCOPE_CONFIG.embedding_model。

- Hugging Face 模型 401 或 gated repo 提示
  - 原因：你使用的是受限模型（例如 google/embeddinggemma-300m）。
  - 解决：
    - 更换为公开可用的模型（如 sentence-transformers/all-MiniLM-L6-v2、BAAI/bge-base-zh-v1.5 等）；或
    - 配置 HUGGING_FACE_HUB_TOKEN 或执行 huggingface-cli login，并确保账号已获模型访问权限。

- 更换向量模型后结果异常/报错
  - 删除 ./data/vector_stores 并重建索引。

- 代理问题
  - 在 llm_config.yaml 配置 HTTP_PROXY/HTTPS_PROXY，程序会在启动时注入环境变量。

## 七、输出格式控制

- 通过以下配置影响输出清洗：
  - LLM_MAX_PARTS_NUM：每个段落最多条目数
  - LLM_MAX_PART_LENGTH：每条条目最大字符数（中文按字符截断）
- system_prompt.yaml 中 {MAX_PARTS_NUM}/{MAX_PART_LENGTH} 会根据配置自动渲染；清洗逻辑也会基于这些限制整理输出。

---

如需进一步扩展（新增云厂商、细化限流/重试策略、启用流式输出等），可在 llm_provider_factory.py 中按现有模式新增 Provider 构建方法，并在配置文件中新增对应配置块即可。
