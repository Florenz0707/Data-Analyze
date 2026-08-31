# M3 限流实现报告

## 1. 为什么需要实现

原有 `check_rate_limit` 只按 API Key 计数，并依赖进程内线程锁；请求链路没有调用它，因此既不能防止登录暴力尝试，也不能在多 worker 部署中形成一致的限额。M3 需要把限流变成认证和接口处理的实际前置条件，并让超额请求具有稳定的错误语义。

## 2. 当前实现

新增 `RateLimitBucket` 数据表，以 `scope + subject + window_start` 为唯一键保存固定窗口计数。每次请求在一个数据库事务中创建或锁定所有相关 bucket，先检查所有维度，再一次性递增；任一维度超限时不递增，返回窗口剩余秒数。PostgreSQL 和 MySQL 的共享数据库连接可跨进程提供一致的行锁语义。

限流 subject 在落库前使用 SHA-256 摘要，避免把用户名和 IP 明文写入限流表。默认不信任 `X-Forwarded-For`；只有显式开启 `RATE_LIMIT_TRUST_PROXY` 时才使用代理转发的首个地址。

## 3. 接口分级与维度

| scope            |     默认限额 | 维度          | 触发位置                        |
| ---------------- | -----------: | ------------- | ------------------------------- |
| `login`          |   10 / 60 秒 | 用户名 + IP   | 登录认证前                      |
| `refresh`        |   20 / 60 秒 | IP            | Refresh Token 交换前            |
| `chat`           |   60 / 60 秒 | 认证用户 + IP | `/api/llm/chat` 认证阶段        |
| `model_validate` |    5 / 60 秒 | 认证用户 + IP | `POST /api/llm/extern` 认证阶段 |
| `api`            | 5000 / 60 秒 | 认证用户 + IP | 其他受保护接口                  |

超额统一返回 HTTP `429`、`code=RATE_LIMITED` 和 `Retry-After`。`Retry-After` 是当前固定窗口结束前的整数秒数，最小为 1。

## 4. 配置

可通过环境变量覆盖以下设置：

```text
RATE_LIMIT_MAX=5000
RATE_LIMIT_INTERVAL=60
RATE_LIMIT_LOGIN_MAX=10
RATE_LIMIT_LOGIN_INTERVAL=60
RATE_LIMIT_REFRESH_MAX=20
RATE_LIMIT_REFRESH_INTERVAL=60
RATE_LIMIT_CHAT_MAX=60
RATE_LIMIT_CHAT_INTERVAL=60
RATE_LIMIT_MODEL_VALIDATE_MAX=5
RATE_LIMIT_MODEL_VALIDATE_INTERVAL=60
RATE_LIMIT_API_MAX=5000
RATE_LIMIT_API_INTERVAL=60
RATE_LIMIT_TRUST_PROXY=false
```

生产环境应使用 PostgreSQL/MySQL 等共享数据库，并结合网关层限流、连接池和清理旧窗口记录。当前实现是固定窗口，不是滑动窗口或令牌桶；窗口边界可能允许短时突发，后续可按成本、租户和模型再细分策略。

## 5. 验收结果

- 服务层限流测试覆盖用户/IP 双维度原子递增、突发超额拦截和窗口重置。
- API 测试覆盖登录、聊天、模型验证三个独立策略及 `429`/`Retry-After`。
- 后端全量测试：72/72 通过。
- `makemigrations --check --dry-run`：无模型漂移。
- 新增迁移：`0011_ratelimitbucket.py`。

普通测试使用临时 SQLite 验证协议和事务流程；SQLite 不代表生产多连接锁竞争。当前 PostgreSQL 已应用 0001–0011，跨进程高并发压测和 MySQL 迁移矩阵仍属于后续验收范围。

## 6. 进一步改进

1. 在 API Gateway/Redis 中增加高吞吐令牌桶或滑动窗口，数据库限流作为一致性和审计补充。
2. 增加租户、模型、成本单位和管理员豁免等维度，并将策略集中配置化。
3. 定期删除过期 bucket，或按窗口使用 Redis TTL，避免数据库表无限增长。
4. 补充 PostgreSQL 多进程、MySQL 并发锁竞争和故障降级压测，记录 P95 延迟、吞吐和误拦截率。
5. 对代理部署建立可信代理列表；不要仅依赖可伪造的转发头识别真实客户端。
