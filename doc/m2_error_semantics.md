# M2：错误语义区分

更新时间：2026-08-30

本文记录 M2 中“错误语义区分”子任务的实现、验证结果和边界。错误响应只返回可操作的业务信息，不返回异常堆栈、Token、Prompt 或外部密钥。

## 1. 为什么需要实现

原 API 只有自由文本 `error`，同样的 HTTP 状态可能代表不同问题，且 Django Ninja 默认认证/参数异常会返回另一种 `detail` 结构。前端无法稳定判断应该重新登录、修正输入、稍后重试，还是切换模型。

因此错误响应需要同时满足：

- HTTP 状态表达协议层结果；
- 稳定错误码表达机器可判断的业务语义；
- `error` 保留人类可读文本，兼容现有调用方；
- 参数校验可携带 `details`，而内部错误不泄露实现细节。

## 2. 项目如何实现

### 2.1 统一响应结构

`deepseek_api.schemas.ErrorResponse` 的结构为：

```json
{
  "code": "VALIDATION_ERROR",
  "error": "请求参数校验失败",
  "details": []
}
```

`details` 仅在请求 Schema 校验失败时返回，普通业务错误可以省略。旧客户端继续读取 `error` 不受影响，新客户端应优先读取 `code`。

### 2.2 错误码和状态码

| 错误码               |    HTTP | 语义与处理建议                                          |
| -------------------- | ------: | ------------------------------------------------------- |
| `VALIDATION_ERROR`   |     400 | 修正请求体、查询参数或游标后重试                        |
| `AUTH_REQUIRED`      |     401 | 未登录，跳转登录                                        |
| `AUTH_INVALID`       | 401/403 | Token、API Key 或 Refresh Token 无效/过期，清理认证状态 |
| `AUTH_FORBIDDEN`     |     403 | 账号被禁用或无权执行操作                                |
| `RESOURCE_NOT_FOUND` |     404 | 会话或模型配置不存在                                    |
| `RESOURCE_CONFLICT`  |     409 | 用户名或 Session 已存在                                 |
| `RATE_LIMITED`       |     429 | 请求过频，遵守 `Retry-After` 后重试                     |
| `MODEL_UNAVAILABLE`  |     503 | 模型或外部模型端点不可用，可稍后重试或切换模型          |
| `INTERNAL_ERROR`     |     500 | 服务内部异常，稍后重试并由服务端排查日志                |

错误码集中定义在 `deepseek_api/errors.py`，业务视图通过 `error_payload()` 创建响应，避免各路由自行拼写错误结构。

### 2.3 全局异常处理

API 注册了 Django Ninja 异常处理器：

- `AuthenticationError`：区分无 Authorization 头的 `AUTH_REQUIRED` 与无效/过期 API Key 的 `AUTH_INVALID`；
- `ValidationError`：统一为 400 `VALIDATION_ERROR`，携带字段定位 details；
- `Throttled`：统一为 429 `RATE_LIMITED`，透传 `Retry-After`；
- `Http404` 和其他 `HttpError`：映射到资源或协议错误码；
- 未捕获异常：记录服务端堆栈，客户端只收到 500 `INTERNAL_ERROR`。

当前 M2 完成了限流异常的错误表达和响应声明；M3 已将数据库共享限流接入请求链路，具体实现和验收见 `doc/m3_rate_limiting.md`。

### 2.4 前端处理

`frontend/vue_frontend/src/api/errors.js` 根据稳定错误码映射可操作提示，并在登录、聊天、会话和模型相关 Store 中统一使用。未知错误码仍回退到后端 `error` 文本，再回退到通用提示。

## 3. 相关模型和数据流

本子任务不新增数据库模型。错误从产生到展示的链路为：

```text
路由/参数/认证/Provider
  ├─ 业务可预期错误 → error_payload(code, message)
  ├─ Ninja 校验或认证异常 → 全局异常处理器
  └─ 未知异常 → 记录服务端日志 + INTERNAL_ERROR
                         ↓
                 ErrorResponse(code, error, details?)
                         ↓
                 前端按 code 给出下一步操作
```

模型不可用使用 503，不再与用户输入错误共用 400；外部模型探测失败也不保存不可用配置。

## 4. 验证结果

API 定向测试：25/25 通过，覆盖：

- 缺失、错误、过期 API Key 的 401 区分；
- 注册、登录、Session、游标和请求体的 400/409 参数语义；
- 禁用账号和 Refresh Token 的认证语义；
- Chat 和外部模型端点不可用的 503；
- Ninja 参数校验的统一 `details`；
- Throttled 的 429 与 `Retry-After`；
- 未捕获异常的安全 500 响应。

前端错误码映射和 API 客户端回归测试随前端测试套件验证。完整后端、前端构建、覆盖率和 pre-commit 结果登记在 `doc/development_plan.md` 的 E-017。

## 5. 进一步改进

1. 将限流真正接入认证/聊天/模型探测链路，按用户、IP 和接口类型分别计数，并使用 Redis 等共享存储。
2. 为错误码建立 OpenAPI 生成的前端类型，避免字符串散落；同时补充错误码版本兼容策略。
3. 增加统一 request/trace ID，并在错误日志中关联它，但不把敏感请求内容写入日志。
4. 对 Provider 超时、认证失败、配额耗尽和上游 5xx 进一步细分内部原因，客户端仍保持稳定的公开错误码集合。
5. 在生产 DEBUG=false、代理和真实共享存储环境中做异常脱敏与错误率告警演练。

## 6. 变更边界

本次完成错误响应结构、稳定错误码、状态声明、Ninja 异常映射和前端提示；实际限流、错误重试/熔断、完整 trace 体系和生产故障演练仍按后续 M2/M3/M7 任务推进。
