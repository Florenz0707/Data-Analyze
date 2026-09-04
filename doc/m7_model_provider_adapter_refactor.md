# M7 模型 Provider Adapter 重构报告

> 实施日期：2026-09-04（Asia/Shanghai）
>
> 状态：Adapter + Registry + Capability 小步重构完成；Model Router、Fallback Router、Agent Runtime 不在本次范围内。

## 1. 目标与结论

将原 `llm_provider_factory.py` 中按 Provider 分支构建模型的逻辑拆分为独立 Adapter，并通过 Registry 统一查找。Factory 继续保留兼容入口，Runtime 继续负责模型实例缓存、HTTP Client 复用、健康状态和生命周期。

本次完成后，新增 Provider 不需要修改 `build_llm_by()` 或 `build_embedding_by()` 的主分支逻辑，只需实现 Adapter、声明元数据和注册 Adapter。

## 2. 模块职责

```text
RAG / Service
      |
      v
llm_provider_factory.py       兼容入口与 Provider Bundle 组装
      |
      v
model_providers/registry.py   Provider 名称、别名和 Adapter 查找
      |
      v
model_providers/*             SDK/配置差异适配，返回 LangChain 对象
      |
      v
LangChain / Provider SDK

model_runtime.py               模型缓存、HTTP Client、健康状态、生命周期
```

Adapter 不负责模型缓存、重试路由、Fallback、Agent 编排或索引集合命名。

## 3. 实现内容

新增 `backend/django_backend/model_providers/`：

- `base.py`：`ProviderAdapter` 协议、`ModelCapabilities` 和 `ProviderMetadata`；
- `registry.py`：内置 Adapter 注册、名称归一化、别名查找和 Provider 集合查询；
- `transformers.py`：Transformers LLM/Embedding；
- `ollama.py`：Ollama LLM/Embedding 与 JSON mode；
- `openai_compat.py`：OpenAI-compatible Chat/Embedding 与共享 HTTP Client；
- `dashscope.py`：DashScope Chat 和 OpenAI SDK Embedding 实现。

`model_runtime.py` 的模型字段和 endpoint identity 解析已改为读取 Adapter 元数据。`configuration.py` 的 Provider 白名单、模型字段和 Embedding dimensions 校验也改为读取 Registry，避免同一 Provider 在 Factory、Runtime 和配置校验中重复维护映射。

## 4. 兼容性约束

以下接口和配置保持不变：

- `build_llm_by(provider, env_cfg, model=...)`；
- `build_embedding_by(provider, env_cfg, model=...)`；
- `build_providers(env_cfg, llm_model=..., embedding_model=...)`；
- `LLM_PROVIDER`、`EMBEDDING_PROVIDER`；
- `OLLAMA_CONFIG`、`TRANSFORMERS_CONFIG`、`OPENAI_COMPAT_CONFIG`、`DASHSCOPE_CONFIG`；
- `hf` Embedding 别名；
- Runtime 的 provider/model/endpoint 缓存隔离；
- `build_providers()` 返回字段和 Embedding collection naming 规则。

Provider SDK 均保持函数内延迟导入，避免应用导入阶段加载 Torch、模型或发起网络请求。

## 5. Capability 语义

每个 Adapter 声明以下能力：

- `streaming`；
- `structured_output`；
- `tool_calling`；
- `json_mode`。

本次仅做声明，不让 Capability 改变现有 RAG Pipeline 分支。后续使用时仍需结合具体 LangChain 对象和 Provider 版本进行运行时验证，尤其要区分“原生结构化输出”和“返回 JSON 文本”。

## 6. 测试与证据

新增 `deepseek_project.tests.test_model_providers`，覆盖：

- 四种内置 Provider Registry 查找；
- `hf` Embedding 别名归一化；
- 未知 Provider 明确异常；
- 模型字段和 endpoint identity 解析；
- Factory 到 Adapter 的 LLM/Embedding 委托；
- Capability 声明读取。

验证结果：

```text
Provider/配置/Runtime 定向测试：31/31
后端应用全量测试：146/146
pre-commit run --all-files：通过
git diff --check：通过
```

全量后端测试使用 `DJANGO_TESTING=1` 和仓库 SQLite 示例配置，未依赖真实模型 API 或生产数据库。

## 7. 新增 Provider 流程

1. 在 `model_providers/` 新增 Adapter 文件；
2. 声明 `ProviderMetadata`，包括配置段、模型字段、别名和能力；
3. 实现 `build_llm()` 与/或 `build_embedding()`；
4. 在 `registry.py` 注册 Adapter；
5. 增加 Registry、构建参数、错误语义和 Runtime 缓存隔离测试；
6. 增加配置模板和文档；
7. 执行后端全量测试与 pre-commit。

## 8. 不在本次范围内

- Model Router；
- Fallback Router；
- Provider 间自动重试策略；
- Agent Runtime 和 Tool Calling 执行器；
- `ChatRequest`/`ChatResponse` 协议重定义；
- 大规模 RAG Pipeline 改造；
- 真实 Provider API 质量和延迟评测。
