# M3：Token 生命周期

更新时间：2026-08-31

本文记录 Access/Refresh Token 生命周期子任务的实现、验证结果和边界。限流、外部模型密钥保护与 SSRF 防护属于 M3 的其他子任务。

## 1. 为什么需要实现

原实现使用 `random.choice` 生成 Token，Access Token 默认有效期约 4 天；Access Token 过期时认证逻辑会删除整条 `APIKey`，导致有效 Refresh Token 也无法恢复；Refresh Token 刷新后不轮换，因此泄漏的旧 Token 可以长期重放。前端收到 401 后也直接退出，没有利用已有的 Refresh Cookie。

## 2. 项目如何实现

### 2.1 Access Token

- `APIKey.generate_key()` 使用 `secrets.token_urlsafe()`，再截取到数据库字段允许的长度；
- `TOKEN_EXPIRY_SECONDS` 默认 900 秒，可通过 `ACCESS_TOKEN_EXPIRY_SECONDS` 配置；
- Access Token 过期只返回 `AUTH_INVALID`，不删除 APIKey，保留 Refresh Token 恢复能力；
- logout 设置 `APIKey.revoked_at`，认证和 Token 校验均拒绝已撤销 Token；
- `APIKey.__str__()` 和初始化命令只输出掩码/状态，不输出完整 Token。

### 2.2 Refresh Token 轮换与重用检测

新增 `RefreshToken` 表，每次签发一个记录：

| 字段               | 作用                                     |
| ------------------ | ---------------------------------------- |
| `token_hash`       | 仅保存 SHA-256 摘要，不保存新 Token 明文 |
| `family_id`        | 标识同一登录会话的 Refresh Token 家族    |
| `expires_at`       | 绝对过期时间，轮换不会无限延长生命周期   |
| `used_at`          | 标识该 Token 是否已经被消费              |
| `revoked_at`       | 标识 Token 或家族是否被撤销              |
| `replaced_by_hash` | 记录轮换后的下一代 Token 摘要            |

刷新流程在事务中锁定 Token 记录：首次使用会标记旧 Token 已消费并签发下一代；再次提交已消费 Token 视为重用，立即撤销整个家族及关联 APIKey。并发提交同一个 Refresh Token 时，数据库行锁保证只有一个请求成功。

为兼容 0001–0008 的旧数据，旧 `APIKey.refresh_token` 会在首次登录/刷新时惰性导入哈希记录；新签发 Token 不再写回该字段。`APIKey.refresh_token` 返回对象上的值只作为当前响应的瞬时值，用于写入 HttpOnly Cookie。

Access Token 会在刷新时重新签发。由于原 `RateLimit.api_key` 外键指向可变的 Token 字符串，新增 0010 将其迁移为稳定的 `APIKey.id` 外键，避免 Token 轮换破坏限流记录归属。

### 2.3 Cookie 与前端恢复

登录和刷新响应均设置：

- `HttpOnly=true`；
- `Secure` 默认跟随 `DEBUG`，可由 `AUTH_COOKIE_SECURE` 覆盖；
- `SameSite` 默认 `Lax`，可由 `AUTH_COOKIE_SAMESITE` 配置；`None` 强制要求 `Secure=true`；
- Cookie 名称可由 `AUTH_REFRESH_COOKIE_NAME` 配置。

Axios 客户端启用 `withCredentials`。收到受保护请求的 401 时，所有并发请求共享一个 Refresh Promise；刷新成功后更新 Access Token 并重放原请求，刷新失败则清理本地状态并跳转登录页。Refresh、登录、注册和 logout 请求不会再次触发刷新循环。

新增 `POST /api/logout`：即使 Access Token 已过期，也会尝试通过 Refresh Cookie 撤销 Token 家族，并始终删除浏览器 Cookie。

## 3. Token 状态转换

```text
签发
  │
  ├─ Access Token 有效 ──> 正常访问
  │
  ├─ Access Token 过期 ──> 401 ──> 有效 Refresh ──> 新 Access + 新 Refresh
  │                                      │
  │                                      └─ 已过期/无效 ──> 清理状态，重新登录
  │
  ├─ 已消费 Refresh 再次出现 ──> 家族重用检测 ──> 撤销整个家族
  │
  └─ logout ──> 撤销 APIKey 与 Refresh 家族
```

## 4. 验证结果

后端测试：

```bash
cd backend/django_backend
DJANGO_DB_CONFIG=config/db_config.yaml.example uv run --project . python manage.py test --noinput
```

结果：67/67 通过，覆盖 Access 过期后仍可刷新、Access/Refresh 轮换、重用检测并撤销家族、logout 撤销、稳定限流外键和错误语义。

前端测试：

```bash
npm test --prefix frontend/vue_frontend -- --run
```

结果：17/17 通过，覆盖 Cookie 凭据、并发 401 单次刷新及请求重放。

迁移验证：

- `0009_apikey_revoked_at_refreshtoken` 和 `0010_ratelimit_stable_apikey_fk` 已生成并应用到当前 PostgreSQL；
- `showmigrations deepseek_api` 显示 0001–0010 全部已应用；
- `makemigrations --check --dry-run` 无变化。

## 5. 边界与后续改进

- 当前 Access Token 仍以 APIKey 字段保存，后续可迁移为仅保存摘要，并通过恒定时间比较或专用 Token 表兼容存量客户端；
- 当前 Refresh 家族状态依赖数据库，水平扩展时必须确保所有 worker 访问同一数据库；
- 前端 Access Token 仍保存在现有 Pinia/LocalStorage 兼容结构中，生产环境可改为仅内存存储；
- 仍需补充多浏览器/多设备会话管理、设备级撤销、审计事件、限流、CSRF 策略和真实部署压测；
- Refresh Cookie 的跨站部署应明确 CORS、CSRF 和 HTTPS 配置，不能只依赖 `SameSite`。
