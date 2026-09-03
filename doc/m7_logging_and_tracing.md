# M7 日志和追踪实现报告

> 采集日期：2026-09-03（Asia/Shanghai）
>
> 状态：M7“日志和追踪”子任务完成；指标、健康检查、部署和故障演练仍待后续验收。
>
> 后续已补充后端 JSONL 持久化、按级别分流和定时轮换，详见 `doc/m7_persistent_structured_logging.md`。

## 1. 范围与结论

本次只执行 M7 的“日志和追踪”板块，目标是让一次请求能够通过 Trace ID 关联 HTTP 生命周期、认证、历史选择、检索、模型调用、结构化解析和前端错误。实现不改变模型、缓存和 API 业务契约，也不把完整用户输入、Prompt、Token 或外部 API Key 写入日志。

已完成的能力：

- Django 请求生成或安全接收 Request ID/Trace ID，并在响应返回 `X-Request-ID`、`X-Trace-ID`；跨域配置允许前端读取这两个响应头。
- 使用 `contextvars` 绑定请求上下文，以一行 JSON 日志输出时间、级别、logger、事件、阶段耗时和 Trace ID。
- 记录认证、历史选择、检索、模型流水线和解析阶段的耗时；检索日志包含证据文档 ID、分数、索引版本和缓存命中状态。
- 记录 provider、model、Prompt 哈希、索引版本、结构化输出状态和 Token 字符估算；真实 Provider Token 用量尚未接入，因此字段明确标记为 `character_estimate`。
- 前端 Axios 和 Fetch/SSE 错误读取响应中的 Trace ID，并通过 `__APP_ERROR_REPORTER__` 钩子和 `app:error` 事件发布受限错误报告。默认不主动向第三方或后端上传。
- 默认 JSON formatter 对 Bearer Token、API Key、Cookie、密码、Secret 等字段脱敏，并限制字符串、集合和异常消息长度。

## 2. 请求链路

```text
HTTP request
  -> RequestTraceMiddleware 生成/校验 Request ID + Trace ID
  -> contextvars 绑定上下文
  -> API/auth/history/retrieval/model/parsing 记录结构化事件
  -> response 返回 X-Request-ID + X-Trace-ID
  -> frontend error reporter 关联 response headers
```

请求头只接受 UUID 形状的 Request ID 和 32 位非零十六进制 Trace ID；无效值会被替换，避免日志注入和伪造过长标识。也支持从 W3C `traceparent` 提取 Trace ID。流式响应会在实际迭代完成或异常时再记录请求完成事件，避免在返回 `StreamingHttpResponse` 时过早结束链路。

## 3. 结构化事件契约

所有应用日志通过 `JsonFormatter` 输出，并由 `RequestContextFilter` 注入 `request_id` 与 `trace_id`。当前主要事件如下：

| 事件                   | 关键字段                                                                 |
| ---------------------- | ------------------------------------------------------------------------ |
| `request.started`      | `method`、`path`                                                         |
| `request.completed`    | `method`、`path`、`status_code`、`outcome`、`duration_ms`                |
| `phase.completed`      | `phase`、`duration_ms`、阶段专属字段                                     |
| `generation.input`     | `session_hash`、`prompt_hash`、`prompt_chars`、输入 Token 估算、历史轮数 |
| `generation.completed` | 输出模式、Schema 状态、修复次数、输出字符数、Prompt 版本                 |
| `client.error`         | 前端错误码、受限消息、来源、route、Request/Trace ID                      |

`phase` 当前覆盖 `authentication`、`history_selection`、`retrieval`、`model_pipeline`、`model` 和 `parsing`。检索事件中的 `evidence_ids` 和 `evidence_scores` 用于从 Trace 复核本次回答使用的证据；`index_version` 用于判断索引版本是否一致。

按 `trace_id` 重建请求时，先筛选同一 Trace ID，再按 `timestamp` 和日志接收顺序查看 `request.started`、各个 `phase.completed` 以及 `request.completed`。日志 formatter 不输出完整异常堆栈，只保留异常类型和受限消息；容器平台若需要堆栈，应通过受控的错误采集配置另行处理。

## 4. 脱敏与隐私边界

- 不记录 `Authorization`、Cookie、API Key、Access/Refresh Token、密码和 Secret 的值；敏感字段统一为 `[REDACTED]`。
- 用户名和 Session ID 在应用事件中使用稳定 SHA-256 短摘要；Prompt 只记录 SHA-256 哈希、字符数和 Token 估算，不记录原文。
- Django `request`/`wsgi_request` 对象不序列化，防止 QueryDict、请求头或 Cookie 被隐式写入日志。
- 异常消息和普通字符串分别限长；日志字段中的嵌套 Map/List 具有深度和数量上限。
- 前端错误报告只保留稳定错误码、限长消息、来源、当前路由和响应中的 Trace/Request ID；上报实现失败不会覆盖原始业务错误。

## 5. 变更位置

- 后端上下文、哈希、Token 估算、JSON formatter：`backend/django_backend/deepseek_project/observability.py`
- 请求生命周期和流式完成事件：`backend/django_backend/deepseek_project/middleware.py`
- 日志配置及跨域响应头：`backend/django_backend/deepseek_project/settings.py`
- API 认证、历史选择、生成输入及流式失败事件：`backend/django_backend/deepseek_api/api.py`
- 用户模型调用、检索和解析阶段：`backend/django_backend/deepseek_api/services.py`、`backend/django_backend/topklogsystem.py`
- 前端错误上下文和上报契约：`frontend/vue_frontend/src/api/errors.js`、`frontend/vue_frontend/src/api/client.js`、`frontend/vue_frontend/src/api/llm.js`

## 6. 验证证据

| 验证项                             | 结果                                                          |
| ---------------------------------- | ------------------------------------------------------------- |
| 后端可观测性 + API 定向测试        | 40/40 通过                                                    |
| 前端 Vitest                        | 26/26 通过                                                    |
| Request/Trace 响应头与 contextvars | 单元测试覆盖普通响应和流式迭代                                |
| 日志脱敏                           | 覆盖 Bearer、`api_key`、password、嵌套敏感字段和 request 对象 |
| 前端错误关联                       | Axios 响应头、Fetch Response 和 `client.error` 事件覆盖       |

复现命令：

```bash
DJANGO_TESTING=true DJANGO_DB_CONFIG=config/db_config.yaml.example \
  uv run --project backend/django_backend --frozen \
  python manage.py test deepseek_project.tests.test_observability \
  deepseek_api.tests.test_api --noinput

npm run test --prefix frontend/vue_frontend -- --run
```

## 7. 未完成项与后续边界

本报告不宣称 M7 整体完成。集中式日志存储、采样和保留策略、请求量/错误率/成本指标、告警通知、Provider 分布式 Trace 传播、健康检查、部署回滚和故障演练仍未实现或未验收；本地 JSONL 持久化、级别分流和轮换另见 `doc/m7_persistent_structured_logging.md`。前端目前提供的是稳定的错误报告接口和浏览器事件；生产环境需要由部署方注入 `__APP_ERROR_REPORTER__`，接入具备访问控制、保留期限和密钥过滤的采集端。
