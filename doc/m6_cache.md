# M6：共享缓存实现报告

更新时间：2026-09-02

本文记录 M6“缓存”子任务的实现、验证结果和边界。缓存命中 P95、真实多 worker 并发和长期 Redis 驱逐趋势仍需在固定部署条件下补测。

## 1. 为什么需要实现

原实现使用 Django 默认的进程内缓存，多个 worker 之间不能共享最终回答，也不能合并相同 Key 的并发请求。检索结果没有独立缓存层，重复向量检索会增加 Embedding、Chroma 和重排开销；无上限的缓存对象还可能挤占 Redis 内存。

## 2. 项目如何实现

### 2.1 Redis 共享后端

非测试环境的 Django CACHES["default"] 使用 django.core.cache.backends.redis.RedisCache，默认地址为 redis://127.0.0.1:6379/0，可通过 REDIS_URL 和 CACHE_KEY_PREFIX 配置。测试环境显式使用隔离的 LocMemCache，不依赖外部 Redis。

新增 redis>=5.0.0 运行时依赖并同步 uv.lock。Redis 不可用时，缓存读写和分布式锁都会记录告警并降级为正常模型/检索请求，不把缓存故障传播为聊天故障。

### 2.2 最终答案与检索缓存隔离

- 最终答案继续使用 reply:<sha256> 键，并纳入用户、Session、Prompt、选中历史、模型、Provider、生成参数、Prompt/索引版本和缓存命名空间；普通 Chat 和 SSE 命中语义保持一致。
- 检索结果使用独立的 retrieval:<sha256> 键，纳入查询、索引版本、Embedding Provider/模型、Top-K、阈值、Hybrid/Reranker 参数和 Metadata Filter，不包含用户答案缓存内容。
- 检索缓存只在默认 Embedding 路径启用；显式传入临时 Embedding 时直接检索，避免未知 Embedding 身份串用结果。
- 索引版本、Embedding 模型或缓存 Schema 变化会产生新 Key；回复缓存仍可通过 invalidate_reply_cache 轮换命名空间失效。

### 2.3 同 Key 请求合并

deepseek_project.cache_runtime.get_or_compute() 使用两层保护：同一 worker 以 Future 合并并发生产者；跨 worker 使用 Redis/Django cache.add 原子锁，并在有限时间内等待已生成值。生产者异常会唤醒等待者并保持原错误语义，Redis 锁或缓存失败则回退为可用请求路径。

### 2.4 大对象与监控

缓存对象按 UTF-8 字节数限制，默认单对象最大 262144 字节；最终答案和序列化后的检索结果都执行该限制，超限值不写入缓存。配置文件新增 RETRIEVAL_CACHE_TTL=300 和 CACHE_MAX_OBJECT_BYTES=262144，两者在加载时校验。

可通过以下命令查看进程命中/未命中、写入、请求合并、超大对象和错误计数，以及 Redis used_memory、evicted_keys：

    cd backend/django_backend
    uv run --project . python manage.py cache_status

## 3. 数据流

    Chat 请求
      ├─ reply:<sha256> ── 命中 → 普通 JSON 或 SSE start/delta/done
      │                    未命中 → 同 Key 合并 → 模型生成 → 有界写入
      └─ retrieval:<sha256> ── 命中 → 复用证据列表
                               未命中 → 同 Key 合并 → Chroma/重排 → 有界写入

## 4. 验证结果

定向后端测试：

    cd backend/django_backend
    DJANGO_TESTING=true DJANGO_DB_CONFIG=config/db_config.yaml.example \
      uv run --project . --frozen python manage.py test \
      deepseek_project.tests.test_cache_runtime \
      deepseek_project.tests.test_configuration \
      deepseek_api.tests.test_retrieval_cache \
      deepseek_api.tests.test_services --noinput

结果：49/49 通过。覆盖 Redis/LocMem 配置契约、最终答案与检索缓存隔离、检索缓存复用、Metadata Filter 隔离、单 Key 进程内请求合并、超大对象跳过、缓存故障降级和 TTL/配置边界。

本机 Redis 验证：redis-cli -h 127.0.0.1 -p 6379 ping 返回 PONG；非测试 Django 进程确认后端为 RedisCache，写入并读取临时值成功。

## 5. 当前验收结论

本子任务已完成代码、配置、依赖、管理命令和自动化回归。M6 Checklist 中的共享缓存、答案/检索隔离、事件协议兼容、同 Key 击穿保护、大对象限制和监控入口均有实现证据。

尚未宣称完成的量化指标：缓存命中 P95、Redis 长期驱逐率、真实多进程同 Key 重复生成次数、20 并发错误率，以及缓存不可用故障演练的生产部署数据。

## 6. 边界与后续改进

1. 进程计数器是 worker 本地指标；生产应接入统一指标系统聚合命中率，并持续采集 Redis memory/eviction 曲线。
2. 分布式锁释放当前依赖短 TTL 和 owner 路径；若生成耗时可能超过锁 TTL，应增加 Redis 原子 compare-delete 和锁续期。
3. 需要在实际 ASGI/WSGI 多 worker 部署下重复固定 Key、20 并发和 Redis 重启测试，补充 P50/P95/P99 与错误率证据。
