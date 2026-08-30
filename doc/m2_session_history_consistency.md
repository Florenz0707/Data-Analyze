# M2 Session/History 一致性修复说明

更新时间：2026-08-30
状态：已实现并完成本地验收
关联里程碑：M2「修复正确性、并发与数据一致性」

## 1. 为什么需要实现

原实现把 `History` 对所属会话的引用拆成两个普通字符串：`session_id` 和 `user`。这种设计无法由数据库保证引用完整性，容易产生以下问题：

- Session 删除后，History 不会自动删除，形成用户不可见的孤立数据；
- Session ID 或用户名在迁移、修复和并发写入中不一致时，历史可能无法归属；
- 每个查询都必须重复拼接 `session_id + user` 条件，调用方容易漏掉用户隔离条件；
- 历史接口只能返回正文，前端无法用稳定的历史 ID 做增量分页或去重；
- Chat 先创建 Session、模型失败后直接返回时，会留下没有首条消息的空会话。

本次修复的目标是让数据库关系、请求事务和 API 返回契约共同表达同一个事实：一条 History 必须属于一个真实 Session，且只能通过当前用户拥有的 Session 被读取或删除。

## 2. 项目如何实现

### 2.1 数据模型与迁移

`History.session` 已改为：

```python
models.ForeignKey(
    Session,
    on_delete=models.CASCADE,
    related_name="histories",
)
```

同时，`Session` 新增独立的 `title` 字段（最长 200 字符，默认使用 `session_id`）。标题不再依赖 ID 的命名格式，后续可以独立编辑或国际化。

本轮进一步将 `Session.user` 从用户名字符串改为 Django `User` 外键，并新增：

- `Session.next_history_sequence`：为同一会话分配单调写入序号；
- `History.sequence`：保证同一 Session 内的历史序号唯一；
- `History.message_id`：客户端可选传入的幂等消息 ID，重复提交同一消息时返回已有答案；
- `(session, created_at, id)` 索引：支持复合游标分页。

迁移文件为 `deepseek_api/migrations/0007_session_history_foreign_key.py` 和 `deepseek_api/migrations/0008_session_user_and_history_ordering.py`，执行顺序如下：

1. 添加 `Session.title`；
2. 临时添加可空外键列；
3. 按旧的 `(session_id, user)` 查找对应 Session；
4. 将有效 History 绑定到 Session；
5. 删除找不到所属 Session 的孤立 History；
6. 将外键改为非空级联关系，删除旧字符串列并重建索引。

0008 继续按旧 `Session.user` 用户名解析 `auth.User`，无法解析的 Session 会被删除（其 History 随外键级联删除）；有效 Session 的旧 History 按 `created_at,id` 初始化 `sequence` 和 `message_id`，并同步 `next_history_sequence`。两次迁移都涉及旧字段删除，正式环境升级前需备份数据库，自动反向迁移不作为生产回滚方案。

孤立数据的处理策略是“迁移时删除”，原因是它已经无法证明所属用户和会话。正式环境迁移前必须完成数据库备份；由于旧字段和孤立行会被移除，回滚不能依赖 Django 自动反向迁移，应通过备份恢复，或在保留导出的前提下编写经验证的数据回填脚本。

### 2.2 Chat 事务与并发写入

Chat API 使用 `transaction.atomic()` 和 `Session.objects.select_for_update()` 包住以下操作：

1. 获取或创建当前用户的 Session，并锁定该 Session 行；
2. 检查 `message_id`，重复请求直接复用既有 History；
3. 读取该 Session 的历史上下文；
4. 调用模型或读取缓存；
5. 原子递增 Session 序号，写入新的 History；
6. 更新 Session 的 `updated_at`。

模型调用返回 `RuntimeError` 时显式标记事务回滚，再返回 503。因此，对于新 Session 的首条消息，模型失败不会留下空 Session；已有 Session 也不会新增半条 History。缓存本身属于后续 M2 缓存正确性子任务的范围，当前事务保证的是数据库状态一致性。生产数据库使用 PostgreSQL/MySQL 时，Session 行锁可将同一会话的模型调用和历史写入串行化；SQLite 的 `select_for_update()` 不提供真正的行锁，主要用于开发和测试，仍需真实数据库并发压测。

### 2.3 用户隔离

所有 Session 查询先将当前认证 Token 的用户名解析为 Django `User`，再绑定 `session_id` 和该 User，通过 `History.objects.filter(session=session)` 读取、清空或删除历史。History 不再存储可被调用方修改的冗余用户字段，跨用户访问不存在匹配的 Session 时统一返回 404。

### 2.4 History API 契约与复合游标

`GET /api/sessions/history` 的每个 `turns` 项现在包含：

```json
{
  "id": 12,
  "created_at": "2026-08-29T10:20:30.000Z",
  "user_input": "如何排查超时？",
  "response": "……"
}
```

响应还包含每条 History 的 `sequence`、`message_id`，以及分页游标：

```json
{
  "next_before_id": 12,
  "next_after_id": 12,
  "next_before_cursor": "eyJjcmVhdGVkX2F0Ijoi...",
  "next_after_cursor": "eyJjcmVhdGVkX2F0Ijoi...",
  "has_more_before": false,
  "has_more_after": true
}
```

- 不传游标时按 `(created_at, id)` 升序返回；
- `before_cursor` 获取更早记录，并在返回前恢复升序；
- `after_cursor` 获取更新记录；
- before 与 after 游标不能同时传入，否则返回 400；
- `limit` 约束在 1 到 1000 之间；
- 游标始终在当前用户拥有的 Session 范围内计算，不会跨 Session 或跨用户泄漏记录。

同时提供不透明的复合游标 `next_before_cursor` / `next_after_cursor`。游标内部编码 `(created_at, id)`，查询使用如下逻辑：

- before：`created_at < cursor.created_at`，或时间相同且 `id < cursor.id`；
- after：`created_at > cursor.created_at`，或时间相同且 `id > cursor.id`。

旧的 `before_id` / `after_id` 参数暂时保留兼容，但新客户端应使用复合游标，避免仅按自增 ID 分页无法表达时间排序的问题。`message_id` 可由客户端在 Chat 请求中提供，用于网络重试幂等。

`POST /api/sessions` 支持可选 `title`，未提供时使用清理后的 `session_id` 作为默认标题。

## 3. 相关模型

### 当前模型关系

```text
APIKey(user=username)
  └── 认证请求 → Django User

Session
  ├── session_id + user(FK)：业务唯一键
  ├── title：独立显示标题
  ├── next_history_sequence：并发写入序号
  └── histories ──< History
                    ├── session_id（数据库外键）
                    ├── sequence/message_id（顺序与幂等）
                    ├── id（复合分页游标的一部分）
                    └── created_at
```

仓库中仍保留旧的 `ConversationSession` 模型作为兼容数据结构；本次修复只针对当前 API 使用的 `Session` / `History` 表，不把两套会话体系混用。

## 4. 验收结果

执行条件：Python 3.13、Django 测试独立数据库、Fake/Mock 模型、不访问真实模型和外部网络。

```bash
cd backend/django_backend
DJANGO_TESTING=true uv run --project . python manage.py test
DJANGO_TESTING=true uv run --project . python manage.py makemigrations --check --dry-run
```

结果：

- 后端全量测试：50/50 通过；
- 后端核心模块覆盖率：89%（1059 statements，排除 migrations/tests/asgi/wsgi；50 个测试）；
- `deepseek_api/api.py` 行覆盖率：95%；新增部分游标错误分支尚未全部覆盖；
- Django system check：通过；
- `makemigrations --check --dry-run`：无模型漂移；
- 临时 SQLite 迁移演练：0007 有效旧 History 绑定 1 条、孤立 History 清理 1 条；0008 旧用户名解析 1 条、无对应用户 Session 清理 1 条，并初始化 sequence/message_id；
- Session 删除级联：History 从数据库删除；
- 模型失败事务回滚：新建 Session 和 History 均不保留；
- 多用户访问：另一用户读取或删除会话均返回 404；
- 重复 `message_id`：只生成和写入一次；复合游标 before/after 查询通过。

完整验收证据登记在 `doc/development_plan.md` 的 E-014（基础一致性）和 E-015（本轮改进）。

## 5. 进一步改进建议

1. **用户外键：已实现基础版**。`Session.user` 已迁移为 Django `User` 外键；后续可继续统一 APIKey、ExternalLLMAPI 等模型的用户归属，并评估自定义用户模型。
2. **并发写入：已实现基础版**。Session 行锁、单调序号和 `message_id` 幂等已接入；后续需在 PostgreSQL/MySQL 上做多连接压测，并处理长模型调用带来的锁持有时间。
3. **复合游标：已实现**。History API 新增 `(created_at, id)` 不透明游标，旧 ID 游标仅用于兼容；后续可在跨分片场景引入签名游标和游标过期策略。
4. **保留可审计的孤立数据**：生产环境若有合规要求，可在迁移前将孤立行导出到隔离表或加密归档，而不是直接删除；归档内容不得重新进入普通用户查询。
5. **统一 Session 列表契约**：当前 `/api/sessions` 为保持前端兼容仍返回 ID 列表。后续可增加版本化接口返回 `{id, title, updated_at}`，再迁移前端显示逻辑。
6. **缩短事务持有时间**：模型调用放在事务内能保证“首条消息失败不留空 Session”，但会延长数据库事务。后续可采用状态字段、幂等消息 ID 和提交后补写方案，在一致性与锁竞争之间取平衡。
7. **补充软删除和保留策略**：对有审计需求的系统，可为 Session/History 增加软删除、TTL 和用户级数据导出/删除流程。
