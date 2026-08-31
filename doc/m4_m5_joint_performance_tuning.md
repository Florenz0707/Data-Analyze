# M4/M5 联合性能调优报告

更新时间：2026-08-31

本轮追加验证使用仓库外的当前本地配置文件，但未修改它；配置 SHA-256 为
`081861bd1267b7ddf3eee9ac86c1dccbd7ab8e3d0a12c7e8852eaf7a05d670c9`，模型为
`deepseek-r1:7b`，Embedding 为 `bge-large:latest`，`max_new_tokens=600`。

## 范围

本轮只调整检索输入、Prompt 组装和模型输出协议，不改变数据库迁移或前端行为。比较使用 M0 固定 50 条查询、同一 Ollama `bge-large:latest`、同一 1708 行根目录 CSV 子集和同一 legacy Chroma 集合。

## 已实施的调优

- M4 评测构建器改为按 4 条文档流式 embedding，不再先把全部结构化文档放进 Python 列表；
- M4 生产索引的增量写入改为按批调用 `insert_nodes`，避免每个文档一次 Python/Chroma 写入；
- M5 Prompt 不再重复注入 Evidence，上下文受 `MAX_PROMPT_CONTEXT_CHARS=12000` 约束；
- M5 输出协议改为紧凑字段说明和真实 Evidence ID 示例，修复请求不再重复发送完整证据 Prompt；
- Ollama 默认启用 JSON 输出模式，并将 `format` 纳入回复缓存身份；
- 结构化输出失败最多修复一次，空检索直接拒答，避免无证据模型生成；
- 生成日志改为记录状态、长度和哈希，不记录完整 Prompt、日志或回复。

## 检索实测

证据：`evaluation/m4/evidence/retrieval_joint_tuning.json`。历史基线来自 `evaluation/m4/evidence/retrieval_comparison.json`。

| 路径                   | Recall@1 | Recall@5 | Recall@10 | MRR@10 | 查询耗时 |
| ---------------------- | -------: | -------: | --------: | -----: | -------: |
| Legacy raw vector      |   0.8000 |   0.9500 |    0.9500 | 0.8646 |  0.0965s |
| M4 structured vector   |   0.8000 |   0.9000 |    0.9000 | 0.8458 |  0.0784s |
| M4 structured hybrid   |   0.8250 |   0.9000 |    0.9000 | 0.8563 |  0.0791s |
| M4 structured reranker |   0.7250 |   0.8750 |    0.8750 | 0.7875 |  0.0659s |

与历史实测相比，结构化向量质量和 Hybrid 的相对关系没有变化；本次向量查询约 0.0784s，Hybrid 约 0.0791s。Hybrid 仅改善 Recall@1，Recall@10/MRR 仍没有超过 legacy，因此线上默认继续使用 vector，不切换默认 Hybrid 或 reranker。

本次结构化构建为 203.4s、Embedding 为 186.1s，历史同条件记录为 46.1s/30.0s；运行期间本地 Ollama 同时受到前一轮真实生成评测占用，且两次进程的 `ru_maxrss` 口径不适合直接归因。该结果记录为资源竞争下的回归信号，不作为代码导致的确定性回归结论；后续应在停止其他 Ollama 请求、独立冷进程和重复 3 次条件下复测。

本轮固定配置重跑使用同一 50 条集中的 M4 检索对照，两次质量指标完全一致。结构化构建耗时为 50.77s → 47.69s，Embedding 为 31.60s → 31.09s；这是单次复测差异，评测脚本本身使用直接批量 Chroma 写入，不能把该差异直接归因于生产 `insert_nodes` 改动；查询耗时受本地服务抖动影响，不作显著性结论。证据分别为 `evaluation/m4/evidence/retrieval_fixed_config_baseline.json` 和 `evaluation/m4/evidence/retrieval_fixed_config_optimized.json`。

## Prompt/生成侧微基准

证据：`evaluation/m5/evidence/prompt_performance.json`。

使用 10 条合成证据、未调用模型时：Prompt 组装 P50 约 0.84ms，`<untrusted_evidence>` 只有 1 个，估计移除 9,234 个重复证据字符。这个收益只代表组装开销和上下文长度，不等同于 LLM 首 token 或端到端延迟收益。

真实 M5 7B 固定配置 50 条评测曾因 15 分钟 shell 超时未写出完整报告；这不是接口错误，而是本地模型输出不稳定并反复触发修复。3 条同集 smoke 的优化前结果为首轮通过 0/3、修复后通过 0/3、模型调用 6 次；优化后最终一轮为首轮通过 2/3、修复后通过 2/3、有效引用 2/3、模型调用 4 次、耗时 77.41s。两轮均为同一配置哈希；证据为 `evaluation/m5/evidence/quality_fixed_config_baseline_smoke3.json`、`evaluation/m5/evidence/quality_fixed_config_optimized_v3_smoke3.json`。这组 smoke 不能替代 50 条质量和人工复核，也不能据此宣称完整吞吐已达标。

## 结论与后续

当前安全的默认组合是：M4 vector 检索 + M5 JSON Schema 校验 + 一次修复 + Evidence ID 白名单 + 空证据拒答。下一轮应：

1. 停止并发 Ollama 任务后重复构建基准 3 次，报告中位数和 P95；
2. 让 Ollama/Chat 模型保持 JSON 输出后，先采集 5 条 smoke，再扩展到 50 条固定集；
3. 对 `RESPONSE_TOP_K`、上下文预算和候选倍数做网格实验，联合比较引用支持率、Schema 通过率和延迟；
4. 在独立 Redis/多 worker 条件下复测回复缓存命中和模型实例隔离，再进入 M6。
