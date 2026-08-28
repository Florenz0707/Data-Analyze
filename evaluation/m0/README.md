# M0 可复现基线

本目录保存 M0 的固定评测数据、验证工具和基线证据。评测数据中的日志 ID 使用 `<CSV 文件名>:<六位数据行号>`，行号从 CSV 表头后的第一条数据开始计数。例如 `python_error_doubao.csv:000001`。

## 文件说明

- `dataset.schema.json`：单条评测样本的 JSON Schema；
- `gold_queries.jsonl`：第一版 50 条固定查询，其中 10 条为知识库无证据的 Windows 事件负样本；
- `validate_dataset.py`：检查字段、分类、负样本比例、日志 ID、疑似密钥和双人标注覆盖；
- `test_evaluation_tools.py`：回归测试评测指标在空检索结果、无正样本和无相关日志样本下的边界语义；
- `collect_environment.py`：生成脱敏的环境、模型、数据哈希与 Chroma 清单；
- `run_retrieval_baseline.py`：通过当前 Ollama Embedding 和 Chroma 索引计算 Recall、MRR、NDCG；
- `run_api_baseline.py`：通过 8081/8082 采集回答结构、失败率、缓存与热请求延迟；
- `benchmark_index_build.py`：在临时目录重建索引并记录耗时与进程峰值 RSS；
- `benchmark_service_startup.py`：在 18081 启动独立、无自动重载的 Django 进程并测量就绪时间；
- `annotation_guidelines.md`、`export_human_review.py`：生成双人独立复核所需的回答包和评分口径；
- `cleanup_evaluation_data.py`：导出回答后删除明确命名的本地合成评测账号及数据；
- `evidence/`：本机实测输出，内容不应包含密码、Token 或完整用户对话。

## 执行命令

在仓库根目录运行：

```bash
backend/django_backend/.venv/bin/python evaluation/m0/validate_dataset.py
backend/django_backend/.venv/bin/python evaluation/m0/test_evaluation_tools.py
backend/django_backend/.venv/bin/python evaluation/m0/collect_environment.py
backend/django_backend/.venv/bin/python evaluation/m0/run_retrieval_baseline.py --repeats 2
M0_EVAL_PASSWORD='<仅用于本地评测的密码>' backend/django_backend/.venv/bin/python evaluation/m0/run_api_baseline.py
backend/django_backend/.venv/bin/python evaluation/m0/benchmark_index_build.py
backend/django_backend/.venv/bin/python evaluation/m0/benchmark_service_startup.py
backend/django_backend/.venv/bin/python evaluation/m0/export_human_review.py
backend/django_backend/.venv/bin/python evaluation/m0/cleanup_evaluation_data.py m0_evaluation m0_evaluation_full
```

人工标注达到 20% 双人覆盖后，执行严格验收：

```bash
backend/django_backend/.venv/bin/python evaluation/m0/validate_dataset.py --strict-acceptance
```

## 口径

- Recall@K、MRR@10、NDCG@10 只统计 40 条有相关日志标注的正样本；
- 负样本不进入排序指标。当前检索器没有拒答阈值，会固定返回 Top-K，因此负样本用于后续回答拒答率和阈值评测；
- 重复运行使用同一数据、模型、Collection 和查询集，`metric_spread` 应不超过 0.01；
- 当前 Chroma 索引没有保存源文件和行号，评测器通过与建库逻辑一致的文本序列化反向映射稳定日志 ID；索引 Schema 改造后应直接使用 metadata。
