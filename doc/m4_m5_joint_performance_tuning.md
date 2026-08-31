# M4/M5 联合性能调优报告

更新时间：2026-08-31

## 范围

本轮只调整检索输入、Prompt 组装和模型输出协议，不改变数据库迁移或前端行为。比较使用 M0 固定 50 条查询、同一 Ollama `bge-large:latest`、同一 1708 行根目录 CSV 子集和同一 legacy Chroma 集合。

## 已实施的调优

- M4 评测构建器改为按 4 条文档流式 embedding，不再先把全部结构化文档放进 Python 列表；
- M5 Prompt 不再重复注入 Evidence，上下文受 `MAX_PROMPT_CONTEXT_CHARS=12000` 约束；
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

## Prompt/生成侧微基准

证据：`evaluation/m5/evidence/prompt_performance.json`。

使用 10 条合成证据、未调用模型时：Prompt 组装 P50 约 0.84ms，`<untrusted_evidence>` 只有 1 个，估计移除 9,234 个重复证据字符。这个收益只代表组装开销和上下文长度，不等同于 LLM 首 token 或端到端延迟收益。

真实 M5 7B 模型 smoke test 在 120 秒内未完成，之前的 50 条 live 评测也因单条请求需要长时间生成/修复而未写出完整报告；因此不能宣称真实生成吞吐已经改善。JSON 模式和更短 Prompt 已落地，仍需在模型空闲时采集首 token、完整响应、修复率和 P95。

## 结论与后续

当前安全的默认组合是：M4 vector 检索 + M5 JSON Schema 校验 + 一次修复 + Evidence ID 白名单 + 空证据拒答。下一轮应：

1. 停止并发 Ollama 任务后重复构建基准 3 次，报告中位数和 P95；
2. 让 Ollama/Chat 模型真正使用 JSON 输出后，仅采集 5 条 smoke，再扩展到 50 条固定集；
3. 对 `RESPONSE_TOP_K`、上下文预算和候选倍数做网格实验，联合比较引用支持率、Schema 通过率和延迟；
4. 在独立 Redis/多 worker 条件下复测回复缓存命中和模型实例隔离，再进入 M6。
