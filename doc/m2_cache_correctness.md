# M2：缓存正确性改进

更新时间：2026-08-30

本文记录 M2 中“缓存正确性”子任务的实现、验证结果和后续改进方向。缓存只保存成功的最终回答，不替代 Session/History 的数据库事实。

## 1. 为什么需要实现

回复缓存一旦使用不完整的身份键，就可能把一个用户、Session、模型或知识库版本的回答返回给另一个请求。只按 Prompt 缓存尤其危险：相同问题在不同历史上下文、不同生成参数或不同索引版本下，正确答案可能不同。

此外，永久缓存会让 Prompt 或知识库更新后继续返回旧结果；空字符串和错误负载进入缓存则会把一次故障放大为持续错误。因此，缓存需要同时定义身份、生命周期、失效和成功响应边界。

## 2. 项目如何实现

### 2.1 稳定且完整的缓存键

`deepseek_api.services._build_reply_cache_key()` 将以下字段组成规范化 JSON，再使用 SHA-256 生成 64 位十六进制摘要。原始 Prompt 不直接出现在 Django cache key 中。

| 字段                     | 作用                              |
| ------------------------ | --------------------------------- |
| 用户、Session            | 防止跨用户和跨会话复用            |
| 完整 Prompt、选中的历史  | 区分当前问题和实际注入的上下文    |
| 生成参数                 | 区分温度、Top P、最大输出等变化   |
| Provider、模型、endpoint | 防止不同运行时实例串台            |
| Prompt 版本、索引版本    | Prompt 或知识库重建后自动产生新键 |
| `RESPONSE_TOP_K`         | 区分检索结果组织配置              |
| 缓存键结构版本、命名空间 | 支持键契约升级和批量逻辑失效      |

`api.chat` 会将历史选择策略和选中的历史显式传入缓存服务；Provider 配置中的生成参数由缓存服务按白名单纳入身份，避免把 API key 等敏感配置放进键材料。

### 2.2 TTL 与写入边界

配置文件新增：

```yaml
REPLY_CACHE_TTL: 3600
PROMPT_VERSION: 'm5-v1'
INDEX_VERSION: 'v1'
CACHE_SCHEMA_VERSION: 'm5-v1'
```

`REPLY_CACHE_TTL` 单位为秒，默认 3600；设置为 `0` 时关闭缓存写入。配置加载阶段拒绝负数和非整数，避免误配成永久或异常生命周期。

`set_cached_reply()` 只有在以下条件同时满足时才写入：

- 调用方将响应标记为可缓存；
- 响应是非空字符串；
- TTL 大于 0。

模型异常由 Chat 事务直接返回错误，不调用缓存写入；非字符串错误负载和显式 `cacheable=False` 的响应也会被拒绝。读取到历史遗留的空值或非字符串值时，会删除该条目并按未命中处理。

### 2.3 批量失效

缓存使用共享的逻辑命名空间 `deepseek:reply-cache:namespace`。执行 `invalidate_reply_cache()` 会轮换命名空间，所有旧键立即不可读；旧物理条目由原有 TTL 或后端驱逐机制回收，不需要依赖通配符删除。

知识库、Prompt 或缓存键结构更新后可执行：

```bash
cd backend/django_backend
uv run --project . python manage.py invalidate_reply_cache
```

命名空间必须存储在生产共享缓存后端中；当前默认开发配置使用 Django 本地缓存，适合单进程测试，不提供跨 worker 的共享失效能力。

## 3. 相关模型与数据流

本子任务不新增数据库模型。相关组件关系如下：

```text
Chat 请求
  ├─ Session/History：数据库事实、事务和幂等写入
  ├─ 历史选择 + Prompt 组装
  ├─ cache key：用户/Session/Prompt/历史/参数/模型/版本/namespace
  └─ 最终回答 cache：命中直接返回；未命中调用模型后按 TTL 写入
```

模型实例缓存和最终回答缓存是两层不同缓存：前者保存进程内模型对象，后者保存字符串回答；两者均不能跨不同模型或 endpoint 复用。

## 4. 验证结果

定向测试：

```bash
cd backend/django_backend
DJANGO_TESTING=true uv run --project . python manage.py test \
  deepseek_api.tests.test_services deepseek_project.tests.test_configuration --noinput
```

当前结果：27/27 通过。覆盖了 SHA-256 稳定键、参数和历史隔离、用户/Session 隔离、TTL 传递、空值/错误值拒绝、命名空间失效和配置边界。

随后在后续错误语义改动后再次执行后端全量测试：57/57 通过；当前覆盖率报告为全部源文件 84%，核心模块（`api/errors/models/services/configuration/model_runtime`）88%，`deepseek_api/api.py` 95%。

完整后端测试、覆盖率、前端构建和 pre-commit 的最终结果登记在 `doc/development_plan.md` 的 E-016。

## 5. 进一步改进

1. 将生产缓存切换为 Redis 等共享后端，并增加缓存可用性降级、命中率、未命中率和失效计数指标。
2. 在 M6 增加同一 Key 的请求合并或短时锁，避免缓存击穿导致重复模型生成。
3. 将 Prompt/索引发布流程与 `invalidate_reply_cache()` 或版本递增绑定，避免依赖人工记忆执行命令。
4. 增加最大响应大小和序列化策略，防止超大回答挤占缓存；必要时只缓存经过校验的结构化最终答案。
5. 在 PostgreSQL/Redis 等真实部署组合上做多进程缓存一致性与故障演练；当前 SQLite/LocMemCache 测试不能证明生产级共享语义。

## 6. 变更边界

本次完成 M2 缓存正确性子任务，不包含 M6 的缓存击穿治理、流式事件缓存、Redis 选型和生产压测；实际限流接入和真实多连接并发仍按开发计划继续验收。
