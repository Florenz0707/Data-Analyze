# M5：Prompt、结构化输出与 RAG 质量

更新时间：2026-08-31

## 结论

M5 的生成链路已经切换为“证据标识 → JSON Schema 校验 → 最多一次修复 → 结构化 Markdown 渲染”的协议。对外仍返回兼容的 `reply` 字符串，因此现有聊天历史和前端 Markdown 展示无需迁移；结构化结果和解析诊断保留在 `TopKLogSystem.query()` 的内部结果中，便于评测和审计。

本阶段完成了实现、回归测试和固定集评测工具，但真实模型质量指标仍需在固定模型参数下运行并由非实现者复核，不能用确定性夹具的结果替代人工质量评审。

与 M4 的联合调优记录见 [`m4_m5_joint_performance_tuning.md`](m4_m5_joint_performance_tuning.md)。

固定配置本轮没有修改 `llm_config.yaml`。配置哈希为
`081861bd1267b7ddf3eee9ac86c1dccbd7ab8e3d0a12c7e8852eaf7a05d670c9`，
`max_new_tokens=600`；M5 只调整了 Prompt/修复请求和评测观测逻辑。

## 为什么需要这套协议

原链路接受任意 Markdown，再用正则猜测五个章节。它无法保证原因和步骤有证据来源，模型输出稍有变体就可能丢失信息；同时日志内容可能包含伪造指令，直接拼接到 Prompt 会扩大 Prompt Injection 风险。

M5 将不可信边界前移：检索内容以 `Evidence ID` 和 `<untrusted_evidence>` 包裹，服务端只接受白名单字段和合法 Evidence ID。证据不足时不调用模型，返回低置信度拒答和追问，避免模型凭空补齐结论。

## 实现

### 结构化数据契约

契约定义在 `backend/django_backend/deepseek_project/response_contract.py`：

- `diagnosis`、`mitigations`、`final_fixes` 为有界字符串列表；
- `possible_causes` 包含 `cause`、`confidence` 和 `evidence_ids`；
- `investigation_steps` 包含 `step`、`expected`、`risk` 和 `evidence_ids`；
- `citations` 只允许引用本次检索结果中的 `document_id`；
- `confidence` 只能是 `high`、`medium`、`low`；
- `need_more_information=true` 时必须提供追问。

Pydantic Schema 同时可导出为 JSON Schema，供支持原生结构化输出的模型适配器使用。当前 `TopKLogSystem` 会优先尝试 `with_structured_output()`；普通 LangChain/Ollama 适配器则使用同一 Schema 的 Prompt 协议并由服务端校验。

### 失败语义

1. 空检索结果：直接返回拒答，不调用模型。
2. 首次 JSON 无效：最多调用一次修复请求，修复请求中的旧输出明确标为不可信数据。
3. 修复仍失败或旧模型只输出 Markdown：进入旧 Sanitizer 降级路径，并在 `generation.output_mode=sanitizer_fallback`、`sanitizer_fallback_count` 中标记。
4. 解析诊断只保留错误位置和类型；生产日志只记录长度、哈希、Prompt 版本和状态，不记录完整 Prompt、日志、模型输出或回复。

### Prompt 与缓存版本

`config/system_prompt.yaml` 使用 `m5-v1` 协议，要求模型只返回 JSON、引用 Evidence ID、忽略日志中的指令并在证据不足时追问。`PROMPT_VERSION` 已进入请求缓存身份键；结构化缓存协议使用 `CACHE_SCHEMA_VERSION=m5-v1`。变更 Prompt 必须重新运行固定集对照。

### 固定配置下的生成优化

为适配有限输出预算，首轮 Prompt 使用紧凑字段协议和当前检索结果中的真实 Evidence ID 示例，最多要求一个原因、一个步骤和一条引用；修复请求只携带问题、可用 ID、校验诊断和 4,000 字符以内的旧输出，不重复携带完整证据块。

### 前端与历史兼容

后端将校验后的结构化对象统一渲染成 Markdown，历史表仍保存兼容的 `response` 字符串。前端只按 Markdown 渲染，不通过正则猜测章节；未来需要富交互证据卡片时，可直接消费内部结构化字段并保持 `reply` 兼容。

## 测试与评测

实现级测试：

- `deepseek_project.tests.test_response_contract`：合法字段、未知 Evidence ID、无证据追问；
- `deepseek_project.tests.test_topk_generation_contract`：合法 JSON、一次修复、空上下文拒答、Prompt 不可信边界；
- `evaluation/m5/test_quality_evaluation.py`：固定 50 条 M0 集的评测器回归。

固定集工具：

```bash
uv run --project backend/django_backend python evaluation/m5/run_quality_evaluation.py \
  --output evaluation/m5/evidence/quality_contract.json
```

真实模型采集需要明确的本地模型和索引：

```bash
uv run --project backend/django_backend python evaluation/m5/run_quality_evaluation.py \
  --live --output evaluation/m5/evidence/quality_live.json
```

`evaluation/m5/prompt_injection_cases.jsonl` 保存 8 个固定安全样本，覆盖伪造系统指令、危险命令、伪造 Evidence ID、元数据地址和秘密泄露请求。夹具证据 `evaluation/m5/evidence/quality_contract.json` 的用途是验证评测器和契约门禁，不代表真实模型的原因正确性或步骤可执行性。

## 当前证据

| 项目                            | 结果                                                                                                              |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| M5 契约定向测试                 | 11/11 通过                                                                                                        |
| 后端全量测试（SQLite 测试配置） | 104/104 通过                                                                                                      |
| 固定集规模                      | 50 条：40 正样本、10 负样本                                                                                       |
| 夹具 Schema 首次通过率          | 100%                                                                                                              |
| 夹具无证据拒答/追问率           | 100%                                                                                                              |
| 夹具高危 Injection 成功改变目标 | 0                                                                                                                 |
| 真实模型 50 条固定集            | 15 分钟 shell 超时，未生成完整结果；详见 `evaluation/m5/evidence/quality_live_attempt.json` 和固定配置基线证据    |
| 固定配置 3 条 smoke（优化前）   | Schema 首轮/修复后 0/3、模型调用 6 次；详见 `quality_fixed_config_baseline_smoke3.json`                           |
| 固定配置 3 条 smoke（优化后）   | Schema 首轮/修复后 2/3、有效引用 2/3、模型调用 4 次、77.41s；详见 `quality_fixed_config_optimized_v3_smoke3.json` |
| 真实模型原因人工评分            | 待运行、待非实现者复核                                                                                            |
| 真实引用支持率/步骤评分         | 待运行、待非实现者复核                                                                                            |

## 后续工作

- 用固定模型、温度、Top-p、索引版本运行 `--live`，保存每条失败样本的检索/Prompt/解析阶段；
- 完成固定 50 条 live 运行；当前 15 分钟超时说明本地 7B 模型仍有输出预算/修复延迟风险；
- 先在模型空闲、JSON mode 生效时完成 5 条 smoke，再扩展到完整 50 条；
- 对 M0 基线答案执行原因正确性、引用支持率、步骤风险和无证据拒答的双人或非实现者复核；
- 将固定集质量门槛接入 CI/发布脚本，质量下降超过 5% 或出现高危 Injection 时阻断；
- 在 M4 联合调优时比较 vector、hybrid、reranker 和阈值对引用有效率、首轮 Schema 通过率及端到端延迟的影响。
