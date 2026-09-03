# M7 指标与健康检查实现报告

> 采集日期：2026-09-03（Asia/Shanghai）
>
> 状态：M7“指标与健康检查”子任务完成；告警通知、部署和故障演练仍待后续验收。

## 1. 范围与结论

本次实现为 Django 服务增加无第三方运行时依赖的 Prometheus 文本指标端点，以及区分进程存活、服务就绪和已加载 Provider 状态的健康检查端点。指标记录使用线程安全的进程内注册表，缓存和模型运行时诊断在抓取时读取，避免改变现有 API 和缓存业务语义。

已完成：

- `GET /api/metrics` 输出 Prometheus `text/plain; version=0.0.4` 格式；
- 请求量、延迟、错误结果、阶段耗时、模型调用、Token 字符估算、检索空召回/分数、结构化输出和健康检查结果指标；
- 回复缓存和检索缓存的命中、未命中、写入、异常、过大跳过、同 Key 等待、命中率、Redis 内存和驱逐诊断；
- 进程 RSS、模型缓存条目、Provider Client 缓存条目、模型加载耗时/峰值 RSS、Worker 容量/占用率和队列代理值；
- `GET /api/health/live` 不访问数据库、缓存、索引或模型；
- `GET /api/health/ready` 检查必要配置、数据库 `SELECT 1`、缓存 set/get/delete 和当前索引状态，不就绪时返回 HTTP 503；
- `GET /api/health/providers` 展示配置 Provider 以及已加载模型实例的健康状态，不主动初始化模型。

## 2. 接口语义

| 接口                    | 成功状态 |                    失败状态 | 说明                                                                      |
| ----------------------- | -------: | --------------------------: | ------------------------------------------------------------------------- |
| `/api/health/live`      |      200 |                           - | 只代表进程仍能处理请求；不代表依赖可用                                    |
| `/api/health/ready`     |      200 |                         503 | 配置、数据库、缓存和当前索引全部通过才就绪                                |
| `/api/health/providers` |      200 | 200（诊断结果内标记 error） | 展示 configured、disabled、not_loaded、healthy、unhealthy；不强制加载模型 |
| `/api/metrics`          |      200 |                           - | 无认证指标抓取端点；生产部署需在反向代理或网络层限制访问                  |

就绪检查读取 `INDEX_STATE_FILE` 指向的版本状态文件，仅接受 `current_version` 对应条目为 `ready` 的状态。索引构建采用原子状态切换时，旧版本仍可服务；切换完成前新版本不会被就绪检查误认为当前版本。

## 3. 指标契约

指标名称保持低基数：HTTP 路径优先使用 Django route，无法解析时将数字、UUID 和长十六进制标识归一为 `:id`。Provider、model、phase、cache kind 和 outcome 为有限标签，禁止把用户、Session、Prompt 或 Token 放入标签。

| 指标                                                             | 类型              | 主要标签/含义                          |
| ---------------------------------------------------------------- | ----------------- | -------------------------------------- |
| `deepseek_http_requests_total`                                   | counter           | method、path、status                   |
| `deepseek_http_request_duration_seconds`                         | histogram         | method、path                           |
| `deepseek_http_requests_in_progress`                             | gauge             | 当前请求数                             |
| `deepseek_phase_calls_total` / `deepseek_phase_duration_seconds` | counter/histogram | phase、outcome                         |
| `deepseek_model_calls_total` / `deepseek_model_timeouts_total`   | counter           | provider、model、outcome；超时单独计数 |
| `deepseek_model_*_tokens_estimate_total`                         | counter           | 字符换算的输入/输出 Token 估算         |
| `deepseek_retrieval_requests_total` / `deepseek_retrieval_score` | counter/histogram | 空召回分类和分数分布                   |
| `deepseek_structured_output_total`                               | counter           | structured、fallback 等结果            |
| `deepseek_cache_*`                                               | counter/gauge     | 回复/检索缓存、命中率、Redis 内存/驱逐 |
| `deepseek_process_resident_memory_bytes`                         | gauge             | 进程历史峰值 RSS 的 Linux 字节换算     |
| `deepseek_queue_length` / `deepseek_worker_*`                    | gauge             | 当前同步服务的队列代理、容量、占用率   |
| `deepseek_health_checks_total`                                   | counter           | 检查项和 ok/error                      |

Provider 成本通过 `OBSERVABILITY_PROVIDER_COST_USD_PER_1K` 配置为 `provider=USD/1k` 后估算；未配置时成本为 0。Token 目前是字符数换算的估算值，不是 Provider 账单中的真实 usage，不能用于结算。

## 4. 配置与安全边界

- `INDEX_STATE_FILE`：当前索引状态文件路径，默认指向 `data/vector_stores/.index_state.json`；
- `OBSERVABILITY_WORKER_CAPACITY`：Worker 容量代理值，默认 1，必须为正整数；
- `OBSERVABILITY_PROVIDER_COST_USD_PER_1K`：逗号分隔的 Provider 成本估算配置，例如 `openai_compat=0.002,dashscope=0.001`。

指标端点不返回 Prompt、Token、API Key、用户标识或缓存值；健康接口只返回依赖类型、状态和索引版本/文档数量等运行元数据。公网部署必须通过反向代理、网络策略或采集器 ACL 保护 `/api/metrics` 与健康接口，避免暴露内部拓扑。

## 5. 变更位置

- 指标注册表、标签归一化和 Prometheus 输出：`backend/django_backend/deepseek_project/metrics.py`；
- 存活、就绪和 Provider 检查：`backend/django_backend/deepseek_project/health.py`；
- HTTP 请求生命周期和模型运行时诊断：`backend/django_backend/deepseek_project/middleware.py`、`backend/django_backend/deepseek_project/model_runtime.py`；
- API 路由和配置：`backend/django_backend/deepseek_api/api.py`、`backend/django_backend/deepseek_project/settings.py`；
- 阶段、生成和缓存指标接入：`backend/django_backend/deepseek_project/observability.py`、`backend/django_backend/topklogsystem.py` 及缓存运行时模块。

## 6. 验证证据

| 验证项                             | 结果                                                 |
| ---------------------------------- | ---------------------------------------------------- |
| 指标/健康/API/日志定向后端测试     | 47/47 通过                                           |
| 指标注册表 Counter/Gauge/Histogram | 单元测试覆盖累加、重置、标签转义和桶输出             |
| Readiness                          | 覆盖配置、数据库、缓存、索引全通过和索引缺失返回 503 |
| Provider health                    | 覆盖禁用配置，不初始化模型                           |
| 指标端点                           | 覆盖 HTTP、缓存、进程内存和队列指标                  |

复现命令：

```bash
cd backend/django_backend
DJANGO_TESTING=true DJANGO_DB_CONFIG=config/db_config.yaml.example \
  uv run --project . --frozen python manage.py test \
  deepseek_project.tests.test_metrics_health \
  deepseek_project.tests.test_observability \
  deepseek_api.tests.test_api --noinput
```

## 7. 未完成项与后续边界

当前服务是同步 Django 请求模型，`deepseek_queue_length=0` 和 Worker 使用率是进程级代理，不等同于 Celery/RQ 等真实队列的积压和多 Worker 资源利用率。模型 Token 与成本仍是估算值；指标注册表是单进程、重启即清空，尚未接入长期 Prometheus/OTel 存储。高优告警阈值、通知渠道、采样/保留策略、真实多 Worker 压测、部署 ACL 和故障演练留待部署验收。
