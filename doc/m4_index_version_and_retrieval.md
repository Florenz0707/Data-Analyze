# M4 索引版本和检索实现报告

更新时间：2026-08-31

## 1. 完成范围

本次将清洗/文档构建产物接入可追踪的索引身份和检索契约，覆盖：

- 索引版本包含逻辑版本、数据文件内容哈希、Embedding provider/model/维度/非敏感参数、解析器版本、分块器版本、分块大小和检索参数；
- 新索引使用带版本的 Chroma collection，在构建完成前不改变当前版本指针；
- 版本状态文件采用同目录临时文件、`fsync` 和 `os.replace` 写入；失败状态保留原因摘要，当前可用版本不被覆盖；
- 构建成功后发布新版本，并清理旧的版本化集合；legacy 集合不会被清理；
- 提供 `rebuild_log_index` 和 `index_status` 管理命令；
- 检索支持严格 Metadata 过滤、最低分数阈值和 `no_evidence` 状态；
- `RETRIEVAL_MODE=hybrid` 使用候选集 BM25 与向量分数归一化加权；`RERANKER_ENABLED=true` 使用确定性的词法重排实验。默认仍为向量检索，避免未经固定评测就改变线上行为。

## 2. 设计和运行方式

`TopKLogSystem` 正常启动时优先读取已发布的版本化集合；没有可用状态或集合时兼容读取旧的 `collection_name`。构建新版本时执行：

```bash
cd backend/django_backend
uv run --project . python manage.py rebuild_log_index
uv run --project . python manage.py index_status
```

状态文件默认位于 `data/vector_stores/.index_state.json`，其中 `current_version` 是已验收版本，`versions[version]` 记录 `building`、`ready` 或 `failed` 状态、集合名、文档数和版本清单。失败重启时会删除同版本的失败集合，避免复用不完整向量。

检索配置示例：

```yaml
RESPONSE_TOP_K: 10
RETRIEVAL_MIN_SCORE: 0.0
RETRIEVAL_MODE: vector # vector | hybrid
RETRIEVAL_CANDIDATE_MULTIPLIER: 3
HYBRID_VECTOR_WEIGHT: 0.7
HYBRID_LEXICAL_WEIGHT: 0.3
RERANKER_ENABLED: false
```

检索结果保留 `document_id`、`content`、`score` 和 `metadata`；混合实验额外保留 `vector_score` 与 `lexical_score`。调用方可通过 `retrieval_status` 区分 `ok`、`no_evidence`、`index_unavailable` 和 `retrieval_error`。

## 3. 验证结果

| 项目                                         | 结果 | 证据                                        |
| -------------------------------------------- | ---- | ------------------------------------------- |
| 数据内容变化导致索引身份变化                 | 通过 | `data_pipeline.tests.test_index_version`    |
| 状态文件原子写入、失败不改变 current pointer | 通过 | `data_pipeline.tests.test_index_version`    |
| 旧版本清理不删除 current/legacy              | 通过 | `data_pipeline.tests.test_index_version`    |
| Metadata 过滤和最低分数阈值                  | 通过 | `deepseek_api.tests.test_topklogsystem`     |
| 混合分数、候选倍数和无证据状态               | 通过 | `deepseek_api.tests.test_topklogsystem`     |
| 检索配置规范化和边界校验                     | 通过 | `deepseek_project.tests.test_configuration` |

真实 Ollama 对照结果见 [`evaluation/m4/retrieval_benchmark_report.md`](../evaluation/m4/retrieval_benchmark_report.md)：在与 legacy 相同的 1708 条根目录 CSV 语料上，M4 结构化向量 Recall@10 为 0.9000，Hybrid 为 0.9000，当前 Reranker 实验为 0.8750；相对本次重跑的 legacy Recall@10=0.9500，尚未证明整体质量提升。

实测前同步修正了来源行号契约：`m4-parser-v2` 将首条 CSV 数据记为第 1 行，与 M0 评测 ID 一致，避免因表头偏移造成虚假的质量下降。

固定评测集的 Recall/MRR 和真实临时索引构建/查询开销已在上述 1708 行公平子集中实测；完整 164386 条记录的全量重建资源数据尚未完成，不能将子集结果外推到完整语料。

## 4. 进一步改进

1. 将状态指针迁移到具备并发租约/版本条件更新能力的元数据存储，并为跨进程热切换增加读写锁或 epoch 检查。
2. 对完整语料建立独立的 BM25 倒排索引；当前 BM25 是候选集实验，不能替代完整的双路召回。
3. 接入可配置的 Cross-Encoder Reranker，记录模型、批大小、延迟和失败回退，不在未评测时默认启用。
4. 使用固定评测集比较结构化文档、向量/混合、Reranker、Top-K 和阈值，并把 Recall@5/10、MRR@10、无证据误召回率写入证据文件。
5. 增加增量删除/更新的真实 Chroma 集成测试，以及构建进度百分比和预计剩余时间的查询接口。
