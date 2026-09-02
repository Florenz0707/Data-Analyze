# 配置、Prompt 与 RAG 版本审计及修复

更新时间：2026-09-02

## 结论

本次审计发现并修复了三类运行时问题：本地配置的 Prompt/缓存版本落后于 M5 协议；Embedding 分块大小没有适配 `bge-large` 的上下文限制；部分 CSV 空表头会生成 Chroma 不接受的空 metadata key。

Prompt 版本和配置加载校验已完成，PostgreSQL 的 `data-analyze` schema 已确认生效。2026-09-02 修复稳定文档 ID 冲突后，完整语料已成功构建并发布新的 RAG 版本；全量构建耗时和峰值内存仍未单独记录，固定集上的全语料召回质量仍需优化。

## 发现的问题

### Prompt 与缓存版本漂移

被 Git 跟踪的规范配置和 `config/system_prompt.yaml` 使用 `m5-v1`，而本地忽略配置曾使用 `v1`。这会使运行时 Prompt 同时携带两个版本标识，也会让缓存键错误标记结构化协议版本。

修复内容：

- 本地配置统一为 `PROMPT_VERSION: m5-v1`；
- 本地配置统一为 `CACHE_SCHEMA_VERSION: m5-v1`；
- `load_llm_config()` 默认版本改为 M5 协议版本；
- 当系统 Prompt 声明 `PromptVersion` 时，配置加载阶段强制校验一致性；
- 增加空版本拒绝和版本一致性回归测试。

修改后应执行回复缓存命名空间失效，避免继续读取旧协议缓存。

### RAG 索引状态漂移

运行时根据当前数据、Embedding、解析器、分块和检索参数计算出的索引身份不等于仅有的旧失败记录。旧记录使用 `m4-parser-v1`，当前代码已经是 `m4-parser-v3`；Chroma 中旧版本化集合为空，legacy 集合有 1708 条向量。

因此系统此前实际使用的是：

```text
index_source_version=legacy
```

当前已发布版本为 `idx-69c72b8c2a56c2fea290`，状态为 `ready`，状态指针已切换；失败构建不会手工改写 `current_version`，只有完整构建成功并写入 `status=ready` 后才会切换。

### Embedding 上下文和 metadata 兼容性

原分块上限为 1200 字符。中文文本按字符长度看似不长，但换算成 token 后可能超过 `bge-large` 上下文限制，Ollama 返回 HTTP 400。

此外，部分 CSV 存在空表头，清洗结果会产生空字符串 metadata key，Chroma 会返回 `InvalidArgumentError`。

修复内容：

- 新增 `INDEX_CHUNK_SIZE` 配置并纳入 `IndexSpec`；
- 默认分块上限调整为 200 字符；
- `INDEX_BUILD_BATCH_SIZE` 与 LlamaIndex 的 Embedding 批大小保持一致；
- 清洗层丢弃空 metadata key；
- 严格去重模式将安全 Metadata 纳入稳定文档 ID，并在索引写入前检查 ID 冲突；
- 版本化索引构建命令在未达到 `ready` 时返回失败，而不是静默输出空结果。

2026-09-02 全量清洗复核：246951 个文档块、246951 个唯一块 ID、0 个重复 ID。此前 `idx-27837d7f5ead18a662c2` 在 20704 个块后因 `DuplicateIDError` 失败，半成品未发布。

## 当前配置和状态

| 项目              | 当前值/状态                              |
| ----------------- | ---------------------------------------- |
| LLM               | OpenAI-compatible，`deepseek-v4-flash`   |
| Embedding         | Ollama，`bge-large:latest`               |
| Embedding 维度    | 1024                                     |
| Prompt 版本       | `m5-v1`                                  |
| 缓存 Schema 版本  | `m5-v1`                                  |
| 逻辑索引版本      | `v1`                                     |
| 分块上限          | 200 字符                                 |
| 索引构建批大小    | 32                                       |
| 检索模式          | vector                                   |
| PostgreSQL schema | `data-analyze`                           |
| 当前索引指针      | `idx-69c72b8c2a56c2fea290`，状态 `ready` |

PostgreSQL 只读检查结果：`current_schema=data-analyze`，`search_path=data-analyze`，迁移表和 Session 相关表均位于该 schema。此次没有修改迁移文件。

## 验证记录

- 配置定向测试：16/16 通过；
- 配置、清洗、TopK、索引版本定向测试：32/32 通过；
- 32 条真实文档批量 Embedding：32/32 成功，1024 维；
- 临时 Chroma 写入验证：4/4 成功；
- 完整 246951 个文档块已完成版本化构建并发布；
- 运行时版本与状态指针匹配，真实检索返回 `ok`，全量 Metadata 必需字段缺失数为 0；
- 全量固定集 Recall@10=0.10，跨源干扰和阈值策略仍需后续优化。

## 后续执行

如需在修改 Embedding 配置或分块参数后重新构建，在 Ollama 空闲且外部模型密钥通过交互 Shell 继承后执行：

```bash
cd backend/django_backend
bash -ic 'exec env DJANGO_TESTING=true DJANGO_SETTINGS_MODULE=deepseek_project.settings PYTHONPATH=. .venv/bin/python manage.py rebuild_log_index --config config/llm_config.yaml'
```

成功条件：命令返回 `status=ready`，`.index_state.json` 的 `current_version` 等于当前 `IndexSpec.version`，对应 Chroma 集合文档数大于 0；随后再运行固定集检索和 M5 质量评测，并更新 M4/M5 证据。

数据库密码仍应使用 `${DB_PASSWORD}` 注入，不应将明文凭据写入配置文件或日志。本次未擅自替换现有运行环境密码，以避免在未设置环境变量时造成连接中断。
