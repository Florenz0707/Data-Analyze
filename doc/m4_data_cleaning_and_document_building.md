# M4 数据清洗和文档构建实现报告

## 1. 本次范围

本报告记录 M4 的“数据清洗”和“文档构建”子任务。索引版本和检索实现及固定集实测已在后续报告中记录，见 `doc/m4_index_version_and_retrieval.md` 和 `evaluation/m4/retrieval_benchmark_report.md`。

## 2. 统一 Schema 与解析

新增 `backend/django_backend/data_pipeline/log_documents.py`，将现有多种 CSV 解析为统一的 `CanonicalLogRecord`：

- `document_id`：由规范化内容生成的 SHA-256 稳定 ID；
- `source_file`、`source_row`：保留来源文件和 CSV 数据行号；
- `service`、`level`、`error_code`、`message`、`component`、`cause`、`timestamp`、`language`；
- `metadata`：保留安全的未映射字段，用于过滤和来源引用。

解析器按字段确定性识别标准故障日志、Computer Events 完整/精简导出、Windows Event Log 和 Python Bug-Fix Pair。编码按 UTF-8 BOM、UTF-8、GB18030、Latin-1 的固定顺序尝试；空白、控制字符、日志级别和常见时间格式统一处理。

## 3. 去重和敏感信息隔离

- 普通数据源使用包含服务、级别、消息、错误码、组件、原因、时间和安全 Metadata 的严格去重键；
- Computer Events 完整/精简导出使用两者共有的服务、级别、消息和语言字段去重，避免精简文件缺失字段导致重复保留；
- 完整 `document_id` 仍包含所有规范字段，因此不同记录不会因去重策略丢失其稳定内容身份；
- 密钥、邮箱、Token 和显式用户标识所在记录进入隔离清单，不生成文档；
- 仅含地理位置、ISP 等潜在 PII 字段的记录保留事件正文，但这些字段不会进入 Metadata；
- 隔离清单只记录来源、行号和原因，不保存敏感原文。

## 4. 文档构建

领域模板按“服务、级别、错误码、组件、时间、语言、日志消息、已知原因、其他字段、来源”组织文本。长文本按句子/换行优先、长度兜底分块，默认每块不超过 1200 字符。

每个块都保存：

- 稳定 `chunk_id` 和父级 `document_id`；
- `source_file`、`source_row`、服务、级别、语言等 Metadata；
- `parser_version`、`cleaner_version`、`chunker_version` 和块序号。

`build_document_manifest()` 以内容哈希记录当前文档状态，`diff_document_manifests()` 返回稳定的 upsert/delete ID，可供后续增量索引使用。`TopKLogSystem` 已改为每批 256 个文档块构建索引，不再一次性保留完整 `Document` 列表；检索结果同时返回文档 ID、分数和 Metadata。

可复现命令：

```bash
cd backend/django_backend
DJANGO_DB_CONFIG=config/db_config.yaml.example \
  uv run python manage.py build_log_documents \
  --input data/log \
  --quality-report ../../evaluation/m4/evidence/data_quality_report.json
```

该命令不会修改原始 CSV，也不会自动替换线上 Chroma 集合；`--documents` 可选地输出 JSONL，`--manifest` 可选地输出稳定文档清单。

## 5. 真实数据质量结果

在当前仓库 `data/log` 的 9 个 CSV 上运行，结果保存在 `evaluation/m4/evidence/data_quality_report.json`：

| 指标           |   结果 |
| -------------- | -----: |
| 读取行数       | 174026 |
| 空行           |     62 |
| 清洗后记录     | 164386 |
| 原始重复行     |   9439 |
| 清洗后重复率   |      0 |
| 隔离记录       |    139 |
| 脱敏字段记录   | 161522 |
| 必填字段合格率 |   100% |
| 文档块数量     | 189129 |

原始重复率为 5.42%，主要来自完整/精简 Computer Events 的重复导出和数据源内完全重复行；清洗结果按去重键保持唯一。Windows Event Log 的位置字段被剔除出 Metadata，但事件正文仍被保留。

## 6. 验收

- M4 数据清洗和文档构建定向测试：9/9 通过；
- 本次 M4 相关代码合并后的后端全量测试：98/98 通过；
- `pre-commit run --all-files`、前端 lint/格式检查/构建需在本次变更完成后执行；
- 本任务未新增数据库迁移，也未访问真实外部模型服务。

## 7. 后续边界

索引版本和检索的实现、定向测试与操作命令见 `doc/m4_index_version_and_retrieval.md`；固定集对照结果见 `evaluation/m4/retrieval_benchmark_report.md`。后续仍需在真实全量 Embedding 重建中记录耗时和峰值内存，并优化未超过 legacy 的结构化/Hybrid 质量。
