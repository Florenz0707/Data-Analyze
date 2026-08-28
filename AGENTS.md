# Data-Analyze 开发约束

本文件适用于整个仓库。若子目录存在更具体的 `AGENTS.md`，则子目录规则优先；用户在当前任务中的明确要求始终优先。

## 1. 项目边界与技术栈

- 后端：`backend/django_backend/`，Python 3.13、Django、Django Ninja、LangChain/LlamaIndex、Chroma。
- 前端：`frontend/vue_frontend/`，Node.js、Vue 3、Vite、Pinia、Naive UI。
- 项目文档：`doc/`；开发进度与验收证据统一维护在 `doc/development_plan.md`。
- 数据集、模型文件、向量索引、构建产物和密钥不是源代码，不得直接提交到仓库。

## 2. 依赖与版本基准

- 后端以 `backend/django_backend/pyproject.toml` 和 `uv.lock` 为唯一依赖基准，使用 `uv` 管理环境。
- 前端以 `frontend/vue_frontend/package.json` 和已纳入 Git 的 `package-lock.json` 为唯一依赖基准，使用 `npm`。未经明确批准，不得混用或迁移到 pnpm/yarn，也不得同时更新多种锁文件。
- 增删依赖必须说明用途，更新对应锁文件，并验证干净环境可重复安装。
- 不得通过复制虚拟环境、`node_modules` 或本机绝对路径解决依赖问题。

初始化开发环境：

```bash
uv sync --project backend/django_backend --frozen
npm ci --prefix frontend/vue_frontend
pre-commit install
```

## 3. 标准开发流程

每次开发遵循以下顺序：

1. 阅读需求、相关文档、现有实现和测试，执行 `git status --short`，确认用户已有改动。
2. 在 `doc/development_plan.md` 中定位对应里程碑、验收指标和风险；需要新增范围时先补充计划。
3. 设计最小、可回滚、可验证的改动，明确输入、输出、失败语义和兼容性影响。
4. 实现业务代码，同时补充对应的单元测试、集成测试或回归样例。
5. 运行与改动最接近的测试，再运行受影响模块的构建、静态检查和格式检查。
6. 必须执行 `pre-commit run --all-files`，处理 Hook 自动修改的文件后重新执行，直到全部通过。
7. 更新接口、配置、运维或用户行为相关文档，并在开发计划中登记指标、证据、风险、决策和变更日志。
8. 交付前复查 diff，确认没有密钥、调试代码、生成物、无关格式化或用户改动被覆盖。

禁止使用 `--no-verify`、`SKIP=...` 或删除 Hook 来绕过检查。只有用户明确批准且记录原因、风险和补验计划时才允许临时例外。

## 4. 强制质量门禁

提交前最低验证集：

```bash
pre-commit run --all-files
npm run build --prefix frontend/vue_frontend
```

按改动范围追加：

```bash
# 后端
uv run --project backend/django_backend python backend/django_backend/manage.py test

# 前端静态检查与格式检查
npm run lint --prefix frontend/vue_frontend
npm run format:check --prefix frontend/vue_frontend
```

- Ruff 负责 Python lint、导入排序和格式化。
- ESLint 负责 JavaScript、TypeScript 与 Vue 的语义检查。
- Prettier 负责 JavaScript、TypeScript、Vue、YAML、JSON、HTML、CSS 和 Markdown 等文本格式。
- 新增语言或文件类型时，必须同时更新工具配置与 pre-commit 覆盖范围。
- Hook 若修改文件，必须审阅 diff 并重新运行；一次失败后直接提交不算通过。
- 无法运行某项检查时，不得声称完成；应明确记录未验证项、原因和复现命令。

## 5. 通用编码范式

- 优先小而清晰的模块、显式依赖和单一职责，避免隐式全局状态与跨层调用。
- 先定义数据契约和错误语义，再实现调用方；边界处校验所有外部输入。
- 业务规则放在可测试的服务/领域层，视图、路由和组件只负责协议与展示编排。
- 避免重复实现；共享逻辑提取前须确认确有多个稳定调用方。
- 注释解释“为什么”和约束，不复述代码；命名表达业务含义，禁止无意义缩写。
- 不捕获后静默吞掉异常；日志应包含可定位的上下文，但不得包含密码、Token、Cookie、完整 Prompt 或用户隐私。
- 不为通过检查而扩大忽略规则。必要的局部忽略必须紧邻代码，并写明原因。

## 6. Python、Django 与 RAG 约束

- 遵循 `ruff.toml`；新增代码使用类型标注，公共服务函数说明输入、输出和异常。
- 配置从环境或统一配置对象注入，不在代码中硬编码密钥、地址、端口、模型名和数据路径。
- Django 视图保持轻量；跨多次写操作使用事务；Schema、HTTP 状态码和稳定错误码保持一致。
- 数据模型变更必须包含迁移、数据兼容方案、回滚说明和相关测试。
- 禁止在模块导入阶段加载大模型、构建索引或发起网络请求。
- LLM、Embedding、Retriever 和向量库通过显式依赖传递；不得在请求期间改写共享全局模型状态。
- RAG 变更必须记录数据版本、索引版本、模型/参数和固定评测集结果；不得用少量主观样例宣称质量提升。
- 模型输出和检索文本均视为不可信输入；结构化输出必须校验，工具调用必须设置白名单、超时、预算和审计。

## 7. Vue 与前端约束

- 新组件优先使用 Vue 3 Composition API 与 `<script setup>`；新建业务模块优先 TypeScript。
- 组件负责展示和局部交互，共享状态进入 Pinia，网络调用集中在 API 层，禁止在多个组件重复拼接接口地址。
- 列表使用稳定业务 ID，不使用数组下标充当可变列表的 key。
- 异步交互必须覆盖 loading、空数据、错误、取消/重复提交和恢复路径。
- 禁止直接渲染未经清洗的 `v-html`；Markdown、链接和后端错误文本必须按不可信内容处理。
- 对外配置使用 Vite 环境变量并提供无密钥模板；客户端包内不得包含任何服务端密钥。
- UI 行为变化至少补充 Store/API/组件级测试之一；关键主流程还需端到端验证。

## 8. 测试与验收

- 修复缺陷时先添加能复现问题的测试；新功能覆盖正常、边界、失败和权限路径。
- 测试必须可重复、可独立运行，不依赖真实密钥、外部网络、生产数据或开发者本机缓存。
- LLM、Embedding 和外部服务默认使用 Fake/Mock；需要真实服务的评测与普通测试分离。
- 时间、随机数、并发和缓存相关测试必须控制不确定性，禁止以增加 sleep 掩盖竞态。
- 性能与质量结论必须附运行条件、基线、目标、实测值和证据路径。

一项工作只有在实现、测试、静态检查、格式检查、构建、文档和验收证据均满足范围要求后，才能标记完成。

## 9. Git 与变更安全

- 保留工作区中不属于当前任务的修改；不得擅自覆盖、删除或格式化无关文件。
- 禁止提交 `.env`、密钥、数据库、模型权重、向量索引、日志、覆盖率产物和编辑器缓存。
- 不执行破坏性 Git 操作，不擅自 amend、rebase、force push 或创建提交。
- 提交应保持单一主题；提交信息说明业务结果，避免只写“update”或“fix”。

## 10. 文档同步

以下变化必须同步文档：API/Schema、环境变量、端口、启动方式、数据流、模型与索引策略、安全边界和验收指标。

每次开发完成后，按 `doc/development_plan.md` 的维护规则更新总进度、对应 Checklist、指标台账、验收证据、风险/阻塞、ADR 和变更日志。没有可核查证据的任务不得勾选。
