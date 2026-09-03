# M7 后端持久化结构化日志实现报告

> 采集日期：2026-09-03（Asia/Shanghai）
>
> 状态：后端持久化结构化日志子任务完成；集中式采集、告警和保留策略仍待部署验收。

## 1. 范围与结论

在现有 Request ID/Trace ID、JSON Formatter 和脱敏规则基础上，将后端应用日志持久化为 UTF-8 JSON Lines 文件，并按日志级别分流。文件 Handler 使用 Python 标准库 `TimedRotatingFileHandler`，默认每日 UTC 轮换并保留 14 个历史文件，不新增第三方依赖。

已完成：

- `debug.jsonl`、`info.jsonl`、`warning.jsonl`、`error.jsonl` 四个级别文件；
- 每行是一个可被 Fluent Bit、Vector、Filebeat 或其他采集器解析的 JSON 对象；
- 每个文件使用精确级别过滤：ERROR 文件同时接收 CRITICAL，其他文件不重复接收相邻级别；
- 默认每天轮换，支持通过环境变量调整轮换周期、备份数、时区和日志目录；
- 轮换 Handler 使用 `delay=True`，启动时只创建目录，不提前打开所有文件；
- 测试环境默认关闭持久化文件输出，避免测试污染仓库，生产/开发环境默认开启；
- 继续沿用现有 Trace 上下文、Prompt 哈希、用户摘要和敏感字段脱敏边界。

## 2. 文件布局与级别分类

默认目录为 `backend/django_backend/data/log/`，可通过 `PERSISTENT_LOG_DIR` 覆盖：

```text
data/log/
├── debug.jsonl
├── info.jsonl
├── warning.jsonl
└── error.jsonl
```

| 文件            | 接收级别       | 默认用途                                    |
| --------------- | -------------- | ------------------------------------------- |
| `debug.jsonl`   | DEBUG          | 调试细节；只有日志级别设为 DEBUG 时才会产生 |
| `info.jsonl`    | INFO           | 正常请求、阶段耗时、检索和生成事件          |
| `warning.jsonl` | WARNING        | 降级、缓存异常、配置回退和可恢复问题        |
| `error.jsonl`   | ERROR/CRITICAL | API 异常、Provider 失败和不可恢复错误       |

控制台仍输出 JSON 日志，文件日志与控制台共享 `JsonFormatter` 和 `RequestContextFilter`，因此可以使用相同的 `request_id`/`trace_id` 关联两类输出。

## 3. 轮换策略

默认配置如下：

| 配置项                             | 默认值              | 说明                                             |
| ---------------------------------- | ------------------- | ------------------------------------------------ |
| `PERSISTENT_LOG_ENABLED`           | 非测试环境为 `true` | 设为 `false` 关闭文件 Handler                    |
| `PERSISTENT_LOG_DIR`               | `data/log`          | 持久化日志目录                                   |
| `PERSISTENT_LOG_LEVEL`             | `INFO`              | 应用最低记录级别；设为 `DEBUG` 才会记录 Debug    |
| `PERSISTENT_LOG_ROTATION_WHEN`     | `midnight`          | Python `TimedRotatingFileHandler` 的 `when` 参数 |
| `PERSISTENT_LOG_ROTATION_INTERVAL` | `1`                 | 每几个周期轮换，非法值回退为 1                   |
| `PERSISTENT_LOG_BACKUP_COUNT`      | `14`                | 每个级别最多保留的轮换文件数量                   |
| `PERSISTENT_LOG_UTC`               | `true`              | 是否按 UTC 计算轮换时间                          |

例如，开发环境每小时轮换并保留 48 份：

```bash
PERSISTENT_LOG_ROTATION_WHEN=H \
PERSISTENT_LOG_ROTATION_INTERVAL=1 \
PERSISTENT_LOG_BACKUP_COUNT=48 \
uv run --project backend/django_backend python backend/django_backend/manage.py runserver
```

轮换文件由标准库 Handler 自动生成，达到 `backupCount` 后删除最旧文件。生产环境仍应由外部采集器负责长期保留、压缩、检索和访问控制，不应把应用目录作为无限期日志仓库。

## 4. 结构化与安全边界

- 每行包含 `timestamp`、`level`、`logger`、`message`、`request_id`、`trace_id` 以及受限的事件字段；
- `api_key`、Authorization、Cookie、密码、Access/Refresh Token 和 Secret 字段统一脱敏；
- Prompt 只记录哈希、字符数和 Token 估算，不记录原文；用户和 Session 只记录稳定摘要；
- 异常仅记录类型和受限消息，不写入完整请求对象、Cookie 或认证头；
- 日志目录已加入 `.gitignore`，避免运行时日志进入版本库；
- 按级别分流只改变存储位置，不改变日志事件内容和现有控制台行为。

## 5. 变更位置

- 级别范围过滤器和持久化配置：`backend/django_backend/deepseek_project/observability.py`、`backend/django_backend/deepseek_project/settings.py`；
- 日志回归测试：`backend/django_backend/deepseek_project/tests/test_observability.py`；
- 环境配置说明：`backend/django_backend/README.md`；
- 运行时日志目录忽略规则：`.gitignore`。

## 6. 验证证据

| 验证项             | 结果                                           |
| ------------------ | ---------------------------------------------- |
| 持久化日志定向测试 | 6/6 通过                                       |
| 级别分类           | INFO/WARNING 精确过滤测试通过                  |
| JSONL 落盘         | 读取文件并校验 `level`、`message` 字段通过     |
| 定时轮换           | 强制执行 `doRollover()` 并验证备份文件生成通过 |
| 后端全量测试       | 140/140 通过                                   |
| pre-commit         | 全部 Hook 通过                                 |

复现命令：

```bash
cd backend/django_backend
DJANGO_TESTING=true DJANGO_DB_CONFIG=config/db_config.yaml.example \
  uv run --project . --frozen python manage.py test \
  deepseek_project.tests.test_observability --noinput
```

## 7. 未完成项与后续边界

当前实现是单进程本地文件持久化。多进程部署时每个进程拥有独立 Handler，生产应接入集中式日志采集并设置采样、压缩、保留、访问控制和告警规则。文件系统不可写时，标准 logging 会将 Handler 写入错误交给 logging 错误处理流程，控制台 Handler 仍是独立输出；部署前应验证日志目录权限和磁盘水位。
