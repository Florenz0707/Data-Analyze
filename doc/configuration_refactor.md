# 配置模块重构说明

更新时间：2026-08-30

## 1. 背景与目标

旧实现通过 `config/generate_llm_config.py` 在运行前生成被 Git 忽略的
`llm_config.yaml`，配置契约分散在 Python 字符串、README 和本地文件中；Django
数据库则直接在 `settings.py` 中固定为 SQLite。这样会导致干净检出无法直接知道
完整配置结构，也无法用同一套启动流程切换到 MySQL 或 PostgreSQL。

本次重构的目标是：

1. 取消 LLM 配置生成脚本，使用被 Git 跟踪的 `llm_config.yaml.example` 作为规范；
2. 用独立的 `db_config.yaml` 定义数据库，提供被跟踪的 `db_config.yaml.example`；
3. 在不改变 Django 模型和迁移历史的情况下支持 SQLite、MySQL、PostgreSQL；
4. 让密码等部署差异通过环境变量注入，并保持测试默认不依赖外部数据库。

## 2. 配置文件约定

| 文件                             | 是否跟踪 | 用途                               |
| -------------------------------- | -------- | ---------------------------------- |
| `config/llm_config.yaml.example` | 是       | LLM/Embedding 的完整规范和安全示例 |
| `config/llm_config.yaml`         | 否       | 本地 LLM 覆盖配置；存在时优先读取  |
| `config/db_config.yaml.example`  | 是       | 数据库字段、引擎和连接参数规范     |
| `config/db_config.yaml`          | 否       | 部署实例的数据库连接配置           |

`load_llm_config()` 与 `load_database_config()` 都使用“本地文件优先、`.example`
回退”的解析规则。`DJANGO_DB_CONFIG` 可以显式指定数据库配置路径；相对路径以
后端项目根目录为基准。数据库 YAML 的结构为：

```yaml
DATABASE:
  ENGINE: postgresql
  NAME: data_analyze
  USER: data_analyze
  PASSWORD: '${DB_PASSWORD}'
  HOST: 127.0.0.1
  PORT: '5432'
  CONN_MAX_AGE: 60
  CONN_HEALTH_CHECKS: true
  OPTIONS:
    connect_timeout: 5
```

支持的 `ENGINE` 会被规范化为 Django backend：

| 配置值                    | Django backend                  | 默认端口 | 驱动              |
| ------------------------- | ------------------------------- | -------: | ----------------- |
| `sqlite` / `sqlite3`      | `django.db.backends.sqlite3`    |        - | Python 内置       |
| `mysql`                   | `django.db.backends.mysql`      |     3306 | `mysqlclient`     |
| `postgres` / `postgresql` | `django.db.backends.postgresql` |     5432 | `psycopg[binary]` |

## 3. 实现与运行时行为

- `deepseek_project.configuration` 负责 YAML 读取、环境变量展开、路径解析、引擎
  别名、必填项和连接参数校验。
- `deepseek_project.settings.DATABASES` 只消费 `load_database_config()` 的结果，
  不再内嵌数据库名称、地址或密码。
- SQLite 默认仍指向后端根目录的 `db.sqlite3`，因此测试和本地开发不需要启动外部
  服务；设置 `DJANGO_DB_PATH` 仍可覆盖 SQLite 文件路径。
- MySQL/PostgreSQL 连接由 Django 原生 backend 管理，`CONN_MAX_AGE`、健康检查和
  `OPTIONS` 原样传递。
- 不在日志中打印完整数据库配置；数据库配置不进入 LLM 脱敏摘要。

## 4. 迁移检查结论

本次只更换 Django 的连接配置，没有修改模型字段、表名、约束或索引，因此不需要
新增迁移。现有 `0001`–`0008` 迁移仍是唯一 schema 来源：

- 0007 负责 `History` 到 `Session` 的外键绑定和孤立数据清理；
- 0008 负责 `Session.user` 的 Django User 外键、History 序号/幂等约束和复合游标索引；
- 这些操作由 Django schema editor 翻译到目标数据库，不应为某个数据库复制一套迁移。

切换到生产数据库时仍需先备份，在目标数据库副本执行 `migrate` 和 `makemigrations
--check --dry-run`，验证数据库用户具备 DDL 权限。0007/0008 会清理无法归属的旧
Session/History，迁移前必须确认备份和归档策略。

## 5. 验证与限制

已增加配置解析测试，覆盖规范文件回退、SQLite 路径、MySQL/PostgreSQL 引擎映射、
端口默认值、环境变量展开和无效远程配置；后端全量测试 62/62 通过。当前自动化测试使用独立 SQLite 测试库，没有声称已经
连接真实 MySQL/PostgreSQL；后续应使用容器化数据库做迁移演练、并发锁测试和连接
故障测试。

## 6. 后续改进

- 为生产部署增加环境分层配置和启动前 `check --database default`；
- 在 CI 使用 MySQL/PostgreSQL 服务矩阵执行迁移与 ORM 回归；
- 增加 TLS、连接池、故障重试和连接指标的明确配置；
- 对配置 Schema 使用 JSON Schema/Pydantic 生成文档，避免 YAML 键名长期漂移；
- 未来若引入 Redis 或 Chroma 独立服务，沿用“跟踪 example + 部署覆盖文件”的边界。
