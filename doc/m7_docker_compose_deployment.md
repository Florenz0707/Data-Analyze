# M7 Docker Compose 部署实现报告

> 采集日期：2026-09-03（Asia/Shanghai）
>
> 状态：Docker Compose 部署模板完成；前端镜像已构建，后端镜像在导入 Docker 存储层时触发 daemon `SIGBUS`，生产密钥、真实模型、备份恢复和回滚演练仍需由部署环境验收。

## 1. 范围与结论

新增前端、后端、PostgreSQL 和 Redis 的 Docker Compose 部署方式。Compose 拓扑、发布端口、容器端口、镜像、宿主机配置文件和数据目录均通过 `deploy/.env` 指定；后端运行配置通过 `backend.env`、数据库 YAML 和 LLM YAML 注入；前端构建配置通过 `VITE_API_BASE_URL` 注入。Dockerfile 不声明发布端口，Compose 也不把端口数值写死在服务定义中。

## 2. 文件布局

```text
docker-compose.yml                 # 服务编排，使用变量引用部署配置
.dockerignore                      # 排除密钥、虚拟环境、索引和构建产物
deploy/
├── .env.example                   # Compose 拓扑、端口、镜像和宿主机路径模板
├── backend.env.example             # Django 运行时配置模板
├── db_config.yaml.example           # PostgreSQL 配置，支持 ${ENV_VAR} 展开
├── llm_config.yaml.example          # Provider/模型配置模板
├── backend.Dockerfile
├── frontend.Dockerfile
└── nginx/default.conf.template     # 前端静态文件和 /api 反向代理模板
```

首次使用时复制四个配置模板：

```bash
cp deploy/.env.example deploy/.env
cp deploy/backend.env.example deploy/backend.env
cp deploy/db_config.yaml.example deploy/db_config.yaml
cp deploy/llm_config.yaml.example deploy/llm_config.yaml
```

然后编辑 `deploy/.env`，将 `BACKEND_ENV_FILE`、`BACKEND_DB_CONFIG_FILE` 和 `BACKEND_LLM_CONFIG_FILE` 指向复制后的文件，并替换 `POSTGRES_PASSWORD` 与 `DJANGO_SECRET_KEY`。这些实际配置文件不应提交到 Git。

## 3. 启动与停止

在仓库根目录执行：

```bash
docker compose --env-file deploy/.env config
docker compose --env-file deploy/.env up --build -d
docker compose --env-file deploy/.env ps
docker compose --env-file deploy/.env logs -f backend
docker compose --env-file deploy/.env down
```

`backend` 启动时先执行数据库迁移，再通过已锁定依赖中的 Uvicorn 提供 ASGI 服务；`frontend` 使用 Nginx 托管 Vite 构建产物，并将 `/api/` 代理到后端。前端对外通常只需开放 `FRONTEND_PUBLISHED_PORT`；后端发布端口保留用于调试、健康探针或内部网关接入。

## 4. 配置契约

### 4.1 端口与服务

| 配置项                    | 示例值 | 作用                        |
| ------------------------- | -----: | --------------------------- |
| `BACKEND_PUBLISHED_PORT`  | `8081` | 宿主机发布的后端端口        |
| `BACKEND_CONTAINER_PORT`  | `8000` | 后端容器内 Uvicorn 监听端口 |
| `FRONTEND_PUBLISHED_PORT` | `8082` | 宿主机发布的前端端口        |
| `FRONTEND_CONTAINER_PORT` | `8080` | Nginx 容器监听端口          |
| `POSTGRES_CONTAINER_PORT` | `5432` | Compose 网络内数据库端口    |
| `REDIS_CONTAINER_PORT`    | `6379` | Compose 网络内 Redis 端口   |

发布端口通过 `${PUBLISHED}:${CONTAINER}` 映射；修改端口只需改 `deploy/.env`，同时 Compose 会把后端端口传给 Uvicorn、把前端端口传给 Nginx 模板和健康检查。

### 4.2 后端配置

- `BACKEND_ENV_FILE` 指向 Django 环境变量文件，包括 Secret、Allowed Hosts、CORS、是否启用模型和持久化日志；
- `BACKEND_DB_CONFIG_FILE` 指向数据库 YAML。默认模板连接 Compose 内部 `db` 服务，密码使用 `${POSTGRES_PASSWORD}` 展开；也可替换为 MySQL、外部 PostgreSQL 或 SQLite 配置；
- `BACKEND_LLM_CONFIG_FILE` 指向 LLM/Embedding/索引配置。默认 Provider 为 Ollama，使用 `host.docker.internal` 访问宿主机 Ollama；启用模型前应确认模型服务地址、密钥和数据目录；
- `BACKEND_DATA_DIR` 持久化 SQLite（若选择 SQLite）和向量索引数据；`BACKEND_LOG_DIR` 持久化 M7 JSONL 日志。

### 4.3 前端配置

`VITE_API_BASE_URL` 在前端镜像构建阶段注入。默认 `/api`，由 Nginx 同源代理到后端，因此浏览器无需解析 Compose 服务名；如果接入独立 API 网关，可在 `deploy/.env` 中改为完整 URL 后重新构建前端镜像。

## 5. 健康检查与依赖顺序

- PostgreSQL 使用 `pg_isready`，Redis 使用 `redis-cli ping`；
- 后端依赖数据库和 Redis 健康后启动迁移，并以 `/api/health/live` 作为容器存活检查；
- 前端依赖后端容器健康后启动，Nginx `/health` 用于容器健康检查；
- `/api/health/ready` 仍会额外检查当前 ready 索引。默认数据目录没有索引时，服务进程仍可运行但业务 Readiness 会返回 503；部署前需将已发布索引挂载到 `BACKEND_DATA_DIR`，或执行索引构建流程。

## 6. 数据和安全边界

- 密钥、数据库配置和 LLM 配置通过宿主机文件只读挂载，不复制到镜像层；`.dockerignore` 排除本地 `.env`、数据库、索引、日志和虚拟环境；
- PostgreSQL 和 Redis 默认只通过 Compose 内部网络访问，不发布宿主机端口；需要外部访问时应显式增加受控网关映射；
- 默认示例使用占位 Secret、关闭 LLM，不能直接作为生产配置；生产必须替换 Secret、限制 `DJANGO_ALLOWED_HOSTS`、CORS 和指标/健康接口访问范围；
- `postgres_data`、`redis_data` 是 Docker named volumes，`BACKEND_DATA_DIR` 和 `BACKEND_LOG_DIR` 是宿主机 bind mount，发布前需纳入备份和恢复策略。

## 7. 验证证据

| 验证项                | 结果                                                                 |
| --------------------- | -------------------------------------------------------------------- |
| Compose 配置渲染      | `docker compose --env-file deploy/.env.example config` 通过          |
| 端口变量传递          | Compose 输出显示发布端口、容器端口和健康检查均来自变量               |
| 配置文件挂载          | DB/LLM/backend env/data/log 均为显式可替换路径                       |
| 前端 API 代理         | Nginx 模板使用变量化后端服务名和端口                                 |
| 实际镜像构建          | 前端镜像构建成功；后端依赖安装完成后在镜像导入/解包阶段触发 `SIGBUS` |
| 后端/前端现有质量门禁 | 后端 140/140；前端 Vitest 26/26；Lint、格式、构建和 pre-commit 通过  |

## 8. 未完成项与后续边界

本次已实际执行 `docker compose --env-file deploy/.env.example build`。前端镜像成功生成；后端 195 个锁定依赖（包含 Torch/CUDA 运行时）安装和字节码编译均完成，但在 Docker 导出层解包时返回 `SIGBUS: bus error`，随后 `docker info`、`docker images` 和 `docker system df` 也均以 `SIGBUS` 退出。宿主文件系统仍有约 45 GB 可用空间，因此当前证据指向 Docker daemon/存储后端异常，需重启或修复 Docker daemon 后重试；不能据此宣称后端镜像构建通过。生产发布仍需在目标架构验证镜像构建、数据库迁移、索引挂载、模型连通性、反向代理 TLS、备份恢复和版本回滚。
