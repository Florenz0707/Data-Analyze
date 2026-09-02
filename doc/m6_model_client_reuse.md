# M6：模型和客户端复用实现报告

更新时间：2026-09-02

本文记录 M6“模型和客户端复用”子任务的实现、验证结果和边界。本次不包含 Redis/共享缓存、缓存击穿、最终答案缓存协议和生产级并发压测。

## 1. 为什么需要实现

模型实例和远端 HTTP 客户端如果在每次请求或每个模型选择时重复创建，会反复触发模型加载、TCP/TLS 建连和连接池初始化，增加首请求延迟与内存峰值。另一方面，未限制的本地模型缓存会在多模型切换时持续占用内存；失效模型继续留在缓存中还会让后续请求重复失败。

## 2. 项目如何实现

### 2.1 远端 Provider Client 复用

`deepseek_project.model_runtime.get_cached_http_client()` 增加有界、线程安全的 Provider Client 缓存，使用 `provider + endpoint` 作为非敏感身份键：

- OpenAI 兼容 Chat、OpenAI 兼容 Embedding、DashScope Chat 和 DashScope Embedding 共享同一端点级 `httpx.Client`；
- 每个客户端继续使用既有 SSRF 防护、固定公网解析地址、无代理、无自动重定向、超时和响应大小限制；
- 同一端点的不同模型只创建一个 HTTP 客户端，因此复用同一个连接池；不同 Provider 或端点不会串用；
- 客户端缓存使用 `MODEL_CACHE_MAX_SIZE` 限制容量，淘汰和显式清理时关闭连接池。

模型配置中的 API Key 不参与客户端缓存键。Key 仍由 OpenAI SDK 按请求注入，不会因为共享连接池而跨用户复用认证信息。

### 2.2 本地模型容量限制

LLM 与 Embedding 分别使用 `ModelInstanceCache`，默认 `MODEL_CACHE_MAX_SIZE=4`。缓存具备：

- 相同 Provider、模型和端点只构造一次；
- LRU 淘汰，避免多模型切换无限增长；
- 构造阶段加锁，避免并发请求对同一模型重复加载；
- Transformers/Ollama 实例淘汰或清理时调用其 `close()`（如果适配器提供），释放本地资源。

模型身份仍包含 Provider、模型名和端点；因此用户选择的模型不会落到默认模型或其他端点上。

### 2.3 健康检查、摘除和重建

缓存对象由 `HealthAwareModel` 代理。每次命中缓存时先检查健康状态：

- Provider 若提供 `health_check()`，使用其结果；异常或显式返回 `False` 会判定为不健康；
- 默认不在每个请求前额外发起网络探测，避免健康检查本身放大远端流量；
- `complete()`/`stream()` 调用抛出异常时将实例标记为不健康；
- 下一次命中同一身份时自动摘除旧实例、关闭可关闭的本地实例并重新构造；
- 远端共享客户端由独立 Client 缓存管理，避免淘汰一个模型实例时误关闭仍被其他模型使用的连接池。

### 2.4 加载时间和内存记录

模型或 Embedding 首次构造时记录：

- Provider、模型、端点身份；
- 构造耗时 `duration_seconds`；
- 进程 `ru_maxrss` 峰值（KiB）；
- UTC 加载时间。

通过 `model_load_records()` 读取当前进程的不可变记录，供启动日志、诊断接口或后续 M7 可观测性接入。当前记录不写入数据库，也不包含密钥。

## 3. 数据流

```text
Provider + model + endpoint
          │
          ├─ ModelInstanceCache：有界 LRU、健康检查、失败摘除/重建
          │          └─ HealthAwareModel
          │
          └─ ProviderClientCache：endpoint 级共享 httpx.Client/连接池
                     └─ Chat/Embedding SDK 共用
```

模型缓存和客户端缓存是两层不同的资源缓存：模型按“模型身份”隔离，客户端按“Provider+端点”复用。两者都不保存最终回答，也不替代 M2 的回复缓存。

## 4. 验证结果

定向后端测试：

```bash
cd backend/django_backend
DJANGO_TESTING=true DJANGO_DB_CONFIG=config/db_config.yaml.example \
  uv run --project . python manage.py test \
  deepseek_project.tests.test_model_runtime deepseek_api.tests.test_api --noinput
```

结果：43/43 通过。覆盖内容包括：

- 同一模型并发构造只发生一次；
- 模型、端点和容量边界隔离；
- 不健康实例自动摘除并重建；
- 同一远端端点只创建一个可复用客户端；
- 用户选择模型向 Provider 工厂正确透传；
- 模型加载耗时和 RSS 记录生成；
- 历史接口 `latest` 最新页与游标分页。

静态验证：目标文件 Ruff 检查通过，Python 编译检查通过。测试使用跟踪的 SQLite 配置模板，没有访问真实外部模型服务。

## 5. 当前验收结论

本子任务的代码、回归测试和连接池复用契约已完成。M6 的“使用 Redis 或其他共享缓存”“同 Key 击穿治理”和真实多 worker/20 并发指标仍保持未验收，不因本报告完成而改变状态。

## 6. 边界与后续改进

1. 当前缓存是进程内缓存；多 worker 部署时每个 worker 都有自己的模型和客户端缓存，不能替代共享缓存或跨进程模型协调。
2. 当前默认健康检查是被动的；M7 可增加带超时和预算的独立 Provider health endpoint，并将结果接入指标。
3. `ru_maxrss` 是进程峰值口径，不等于单个模型的精确增量内存；模型加载基准仍需在固定硬件、空闲模型服务和重复 3 次条件下测量 P50/P95。
4. 若未来支持模型热卸载，需要为各 Provider 明确定义关闭协议，并增加关闭期间并发请求的状态转换测试。
