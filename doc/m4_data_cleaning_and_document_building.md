# M4 数据清洗和文档构建实现报告

## 1. 本次范围

本报告记录 M4 的“数据清洗”和“文档构建”子任务。索引版本和检索实现及固定集实测已在后续报告中记录，见 `doc/m4_index_version_and_retrieval.md` 和 `evaluation/m4/retrieval_benchmark_report.md`。

## 2. 统一 Schema 与解析

新增 `backend/django_backend/data_pipeline/log_documents.py`，将现有多种 CSV 解析为统一的 `CanonicalLogRecord`：

- `document_id`：由规范化内容和去重边界生成的 SHA-256 稳定 ID；严格去重模式将安全 Metadata 纳入身份，避免保留的不同记录产生相同 ID；
- `source_file`、`source_row`：保留来源文件和 CSV 数据行号；
- `service`、`level`、`error_code`、`message`、`component`、`cause`、`timestamp`、`language`；
- `metadata`：保留安全的未映射字段，用于过滤和来源引用。

解析器按字段确定性识别标准故障日志、Computer Events 完整/精简导出、Windows Event Log 和 Python Bug-Fix Pair。编码按 UTF-8 BOM、UTF-8、GB18030、Latin-1 的固定顺序尝试；空白、控制字符、日志级别和常见时间格式统一处理。

## 3. 去重和敏感信息隔离

- 普通数据源使用包含服务、级别、消息、错误码、组件、原因、时间和安全 Metadata 的严格去重键；
- Computer Events 完整/精简导出使用两者共有的服务、级别、消息和语言字段去重，避免精简文件缺失字段导致重复保留；
- `document_id` 与去重边界保持一致：严格模式包含安全 Metadata，Computer Events 精简/完整导出使用共享身份；
- 清洗完成后会在任何索引写入前检查稳定 ID 冲突，发现冲突立即失败并报告两条来源位置；
- 密钥、邮箱、Token 和显式用户标识所在记录进入隔离清单，不生成文档；
- 仅含地理位置、ISP 等潜在 PII 字段的记录保留事件正文，但这些字段不会进入 Metadata；
- 隔离清单只记录来源、行号和原因，不保存敏感原文。

## 4. 文档构建

领域模板按“服务、级别、错误码、组件、时间、语言、日志消息、已知原因、其他字段、来源”组织文本。长文本按句子/换行优先、长度兜底分块，当前 Embedding 配置每块不超过 200 字符，以适配 `bge-large` 上下文限制。

每个块都保存：

- 稳定 `chunk_id` 和父级 `document_id`；
- `source_file`、`source_row`、服务、级别、语言等 Metadata；
- `parser_version`、`cleaner_version`、`chunker_version` 和块序号。

`build_document_manifest()` 以内容哈希记录当前文档状态，`diff_document_manifests()` 返回稳定的 upsert/delete ID，可供后续增量索引使用。`TopKLogSystem` 已按配置以每批 32 个文档块构建索引，不再一次性保留完整 `Document` 列表；检索结果同时返回文档 ID、分数和 Metadata。

可复现命令：

```bash
cd backend/django_backend
DJANGO_DB_CONFIG=config/db_config.yaml.example \
  uv run python manage.py build_log_documents \
  --input data/log \
  --quality-report ../../evaluation/m4/evidence/data_quality_report.json \
  --max-chars 200
```

该命令不会修改原始 CSV，也不会自动替换线上 Chroma 集合；`--documents` 可选地输出 JSONL，`--manifest` 可选地输出稳定文档清单。

## 5. 真实数据质量结果

在当前仓库 `data/log` 的 9 个 CSV 上运行，结果保存在 `evaluation/m4/evidence/data_quality_report.json`：

| 指标           |   结果 |
| -------------- | -----: |
| 读取行数       | 174026 |
| 空行           |     62 |
| 清洗后记录     |  73912 |
| 原始重复行     |  99913 |
| 清洗后重复率   |      0 |
| 隔离记录       |    139 |
| 脱敏字段记录   | 161522 |
| 必填字段合格率 |   100% |
| 文档块数量     | 246951 |
| 唯一文档 ID    |  73912 |
| 文档 ID 冲突数 |      0 |

原始重复率为 57.41%，主要来自完整/精简 Computer Events 的重复导出和数据源内完全重复行；清洗结果按去重键保持唯一。Windows Event Log 的位置字段被剔除出 Metadata，但事件正文仍被保留。当前证据使用 `m4-cleaner-v2`、`m4-parser-v3` 和 200 字符分块。此次版本升级修复了严格去重 Metadata 与稳定 ID 身份边界不一致的问题。

## 6. 稳定 ID 冲突修复

此前严格去重键包含安全 Metadata，但 `document_id` 没有包含 Metadata。例如两条消息完全相同、但 `machinename` 不同的 Windows 事件会被清洗层保留，却生成相同的 Chroma `chunk_id`，最终触发 `DuplicateIDError`。

修复后：

- 严格模式的稳定 ID 纳入安全 Metadata 和 `dedupe_mode`；
- 清洗层在返回结果前执行稳定 ID 冲突检查；
- 版本化索引构建只在成功发布后输出“完成”，失败回退只记录旧索引仍在使用；
- 失败版本不会改变 `current_version`，下次构建会删除同名失败集合。

完整语料复核结果为 246951 个文档块、246951 个唯一块 ID、0 个重复块 ID。此前失败版本 `idx-27837d7f5ead18a662c2` 的 20704 条半成品不应继续使用。

## 7. 验收

- M4 数据清洗和文档构建定向测试：8/8 通过；
- 本次 M4 相关代码合并后的后端全量测试：98/98 通过；
- 本次修复后的定向后端测试：13/13 通过；
- 本次修复后 `pre-commit run --all-files` 通过，后端全量测试 119/119 通过；
- 本任务未新增数据库迁移，也未访问真实外部模型服务。

## 8. 后续边界

索引版本和检索的实现、定向测试与操作命令见 `doc/m4_index_version_and_retrieval.md`；固定集对照结果见 `evaluation/m4/retrieval_benchmark_report.md`。全量 Embedding 已完成并发布；后续仍需记录全量构建耗时和峰值内存，并优化全语料检索质量。
