# M4 索引版本和检索实现报告

更新时间：2026-09-02

## 1. 完成范围

本次将清洗/文档构建产物接入可追踪的索引身份和检索契约，覆盖：

- 索引版本包含逻辑版本、数据文件内容哈希、Embedding provider/model/维度/非敏感参数、解析器版本、分块器版本、分块大小和检索参数；
- 新索引使用带版本的 Chroma collection，在构建完成前不改变当前版本指针；
- 版本状态文件采用同目录临时文件、`fsync` 和 `os.replace` 写入；失败状态保留原因摘要，当前可用版本不被覆盖；
- 构建成功后发布新版本，并清理旧的版本化集合；legacy 集合不会被清理；
- 提供 `rebuild_log_index` 和 `index_status` 管理命令；
- 清洗阶段校验稳定文档 ID，避免重复 ID 写入 Chroma；
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

实测前同步修正了来源行号契约：`m4-parser-v2` 将首条 CSV 数据记为第 1 行，与 M0 评测 ID 一致，避免因表头偏移造成虚假的质量下降。随后 `m4-parser-v3` 修复了严格去重 Metadata 与稳定文档 ID 不一致的问题；此前版本的固定集结果仍按历史版本保留，新的全量索引必须使用 v3。

固定评测集的 Recall/MRR 和真实临时索引构建/查询开销已在上述 1708 行公平子集中实测。完整语料现已完成版本化构建，但全量构建耗时和峰值内存未单独记录；全量语料固定集结果见 `evaluation/m4/evidence/retrieval_published_full_index.json`，不应与 1708 行公平子集结果混为一谈。

## 4. 本次失败构建修复

2026-09-02 的全量构建在写入 20704 个文档块后失败，状态原因为 `DuplicateIDError`。问题来自严格去重保留了不同 `machinename` 的记录，而旧稳定 ID 未包含安全 Metadata。修复后，完整 246951 个文档块扫描得到 246951 个唯一 ID；构建逻辑也不再把回退到 legacy 的失败构建记录为“完成”。

失败版本化集合不会发布，`current_version` 不会改变；重新执行构建时会自动清理同版本失败集合。

## 5. 已发布全量索引复核

2026-09-02 重建并发布：

- 当前版本：`idx-69c72b8c2a56c2fea290`；状态：`ready`；
- Chroma 集合：`log_collection_bge_large_latest__idx-69c72b8c2a56c2fea290`；向量数：246951；
- 数据内容哈希：`b43eea2179af90829fd8fffb2335402386167635b842a183e9ba70cfa2695b70`；
- `m4-parser-v3`、200 字符分块、`bge-large:latest`；实际向量维度 1024（当前 Ollama 配置未声明维度，因此 IndexSpec 的维度字段仍为 `null`）；
- 全量 246951 条 Metadata 逐页检查，`document_id/source_file/source_row/parser_version/cleaner_version/chunker_version` 缺失数均为 0；
- 运行时启动和一次真实检索均加载当前版本，检索状态为 `ok`。

在 M0 的 40 条正样本/10 条负样本上直接查询全量集合，Recall@10 和 MRR@10 均为 0.10，负样本默认均会返回候选。该结果反映新增递归语料带来的跨源干扰，不能直接证明索引构建失败；仅筛选原 5 个根目录来源时，诊断性 Recall@10 为 0.80。当前不把 `RETRIEVAL_MIN_SCORE` 盲目调到 0.7/0.8：阈值 0.7 时正样本 Recall 为 0，阈值 0.8 时负样本无证据率为 100% 但同样丢失全部正样本。后续应优化全语料召回策略或扩充覆盖递归语料的评测集。

完整证据见 [`retrieval_published_full_index.json`](../evaluation/m4/evidence/retrieval_published_full_index.json)。

配置审计结论：Prompt、缓存、索引逻辑版本、Prompt 文件版本、数据版本和检索参数均与运行时一致，无需立即修改配置。若要让 IndexSpec 记录明确的 `1024` 维度，应先在 LLM 配置中声明 `OLLAMA_CONFIG.embedding_dimensions: 1024`，再完整重建一次索引；不能只改配置而继续复用当前集合。

## 6. 进一步改进

1. 将状态指针迁移到具备并发租约/版本条件更新能力的元数据存储，并为跨进程热切换增加读写锁或 epoch 检查。
2. 对完整语料建立独立的 BM25 倒排索引；当前 BM25 是候选集实验，不能替代完整的双路召回。
3. 接入可配置的 Cross-Encoder Reranker，记录模型、批大小、延迟和失败回退，不在未评测时默认启用。
4. 使用固定评测集比较结构化文档、向量/混合、Reranker、Top-K 和阈值，并把 Recall@5/10、MRR@10、无证据误召回率写入证据文件。
5. 增加增量删除/更新的真实 Chroma 集成测试，以及构建进度百分比和预计剩余时间的查询接口。
