# M8 只读工具权限与审计方案（已确认方案与实现记录）

状态：权限与审计方案已由用户确认；Workflow 第一阶段实现中。

更新时间：2026-09-04

## 1. 目标与边界

本方案为 M8 第一阶段 Agent Workflow 提供只读工具的权限、参数校验、执行约束和审计契约。工具只能查询已经存在的日志、指标、发布、依赖和事故数据，不执行 Shell、不修改配置、不重启服务、不创建或关闭告警、不提交工单，也不执行任何生产写操作。

第一阶段工具：

| 工具                       | 用途                         | 默认数据范围                   |
| -------------------------- | ---------------------------- | ------------------------------ |
| `search_logs`              | 按服务、时间和关键词检索日志 | 当前租户可见服务，最多 100 条  |
| `query_metrics`            | 查询允许的指标时间序列       | 当前租户可见服务，最多 24 小时 |
| `get_deployments`          | 查询发布记录和状态           | 当前租户可见环境，最多 100 条  |
| `get_service_dependencies` | 查询服务依赖关系             | 当前租户可见服务，最多 2 层    |
| `search_incidents`         | 查询历史事故或工单           | 当前租户可见服务，最多 100 条  |

## 2. 权限模型

权限采用默认拒绝和服务端授权。模型只能提出工具名和业务参数，不能决定调用身份、租户、服务范围、权限或数据脱敏策略。

| 身份                | 允许范围                       | 限制                                                   |
| ------------------- | ------------------------------ | ------------------------------------------------------ |
| `analyst`           | `search_logs`、`query_metrics` | 只能访问已授权服务和脱敏结果                           |
| `operator_readonly` | 五个只读工具                   | 仍不能写入生产系统，服务端强制时间、数量和响应大小上限 |
| 未认证或其他身份    | 无                             | 直接拒绝，不访问下游系统                               |

用户身份、租户、角色和服务授权范围必须从已认证请求上下文派生。工具参数中出现 `actor_user_id`、`tenant_id`、`role`、`environment` 覆盖值时，服务端应忽略或拒绝，而不能将其当作提权依据。

## 3. 调用契约

每次调用都携带服务端生成或继承的 `request_id`、`trace_id` 和 `deadline`。建议统一使用以下外层结构：

```json
{
  "tool_name": "query_metrics",
  "arguments": {},
  "request_id": "server-generated",
  "trace_id": "request-context",
  "deadline_ms": 2000,
  "max_bytes": 262144
}
```

`request_id`、`trace_id`、身份和权限不接受模型填写的值。服务端必须对工具名和参数进行 JSON Schema 校验、授权校验、范围收敛和资源预算校验，然后才允许访问下游查询接口。

## 4. 工具参数约束

下表是实现 JSON Schema 时必须满足的最小约束；具体 Schema 应作为代码中的版本化契约，并为每个工具补充拒绝路径测试。

| 工具                       | 必须校验的字段与上限                                                                                                      |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `search_logs`              | `service` 必须来自授权服务；`start`/`end` 必须是有效时间且跨度不超过 2 小时；`keyword` 最长 200 字符；`limit` 范围 1～100 |
| `query_metrics`            | `metric_names` 必须来自指标白名单；时间跨度不超过 24 小时；`step_seconds` 范围 15～3600；序列数量不超过 20                |
| `get_deployments`          | `service`、`environment` 必须来自授权范围；时间跨度不超过 30 天；`limit` 范围 1～100                                      |
| `get_service_dependencies` | `service` 必须来自授权范围；`direction` 只能为 `upstream`/`downstream`/`both`；`depth` 范围 1～2；节点数不超过 100        |
| `search_incidents`         | 查询文本最长 200 字符；时间跨度不超过 90 天；状态使用白名单；`limit` 范围 1～100                                          |

所有工具还必须限制查询超时（建议默认 2 秒、硬上限 5 秒）、响应大小（建议 256 KiB）、分页数量和下游重试次数。只允许幂等查询，默认不重试；确需重试时最多执行一次，并在审计事件中记录。

## 5. 数据安全与返回格式

- 日志中的 Token、Cookie、API Key、密码、Authorization 和用户隐私必须在工具适配器层脱敏。
- 工具结果应返回来源、查询时间范围、结果数量、是否截断和数据版本，便于 Agent 判断证据是否完整。
- 日志和事故正文属于不可信数据，必须作为数据传递，不能作为新的工具指令执行。
- 超时、拒绝、截断和下游错误使用稳定错误码；不得把下游堆栈或凭据返回给模型。
- 工具响应不直接写入长期记忆或用户可见历史，除非经过现有业务层的明确授权和脱敏流程。

建议结果结构：

```json
{
  "tool_name": "query_metrics",
  "status": "ok",
  "items": [],
  "source": "metrics-read-api",
  "time_range": { "start": "...", "end": "..." },
  "truncated": false,
  "result_count": 0
}
```

## 6. 审计事件

授权判断、实际执行和最终结果各至少产生一条可关联的结构化审计事件。审计事件必须包含：

| 字段                                        | 要求                        |
| ------------------------------------------- | --------------------------- |
| `event_name`、`schema_version`              | 固定事件名和版本            |
| `timestamp`、`duration_ms`                  | 服务端 UTC 时间和耗时       |
| `request_id`、`trace_id`                    | 关联请求和节点执行链路      |
| `actor_user_id`、`tenant_id`、`role`        | 从认证上下文派生            |
| `tool_name`、`tool_version`                 | 工具注册表中的值            |
| `decision`、`reason_code`                   | `allow`/`deny` 及稳定原因码 |
| `arguments_hash`、`redacted_arguments`      | 参数摘要和脱敏后的必要字段  |
| `backend`、`status`、`error_code`           | 下游、结果状态和错误分类    |
| `result_count`、`result_bytes`、`truncated` | 输出规模和截断情况          |

审计日志不得记录 Token、Cookie、密码、完整 Prompt、完整用户输入或未经脱敏的日志正文。事件写入失败不能改变“默认拒绝”的权限判断；对于已执行的查询，应至少输出本地结构化错误并触发运维告警，避免形成无审计调用。

## 7. 拒绝与人工接管

以下情况必须拒绝调用并记录原因：未认证、工具不在注册表、角色无权限、服务或指标不在白名单、时间范围或数量超限、参数 Schema 不合法、deadline 已过期、响应预算不足、下游仅提供写操作，或模型要求覆盖服务端身份字段。

当工具连续超时、返回证据为空、结果被截断或下游不可用时，Workflow 应停止继续扩展工具调用，向用户说明证据不足，并提供人工接管路径。不得为了得到答案而放宽权限、扩大时间范围或自动切换到写操作。

## 8. 评审与实现门槛

在 M8 启动前，评审人需要确认：

- [ ] 角色矩阵与实际认证/租户模型一致；
- [ ] 五个工具的 JSON Schema、白名单和拒绝用例已落地；
- [ ] 服务端身份派生和参数二次校验已有测试；
- [ ] 超时、响应大小、重试和截断行为已有测试；
- [ ] 审计事件字段、脱敏规则、保留周期和检索方式已确认；
- [ ] 拒绝、下游失败和人工接管路径已演练；
- [ ] 只读边界经安全评审确认，且没有生产写入口。

当前结论：权限与审计边界已由用户确认；具体工具适配器、保留策略和完整演练仍需按第 8 节逐项验收，不能因本核心实现完成而宣称 M8 整体通过。

## 9. 第一阶段 Workflow 实现

已实现 `deepseek_project.agent_workflow` 固定编排核心：

```text
输入问题 → 分类(read_only_diagnostic) → 计划(最多 5 步)
  → 服务端授权/Schema/范围校验 → 串行执行只读工具
  → 证据验证 → 完成回答或人工复核接管
```

当前实现包括：

- `ToolRegistry` 只允许方案中的五个工具，默认拒绝未注册工具；
- `ToolContext` 保存服务端派生的用户、角色、服务范围、请求/追踪 ID、Token 和时间预算；
- `ToolRegistry.schemas()` 暴露 `additionalProperties=false` 的版本化 JSON Schema-like 契约；
- 工具执行前拒绝身份覆盖、越权服务、非法指标、超范围时间、非法参数和超步数请求；
- 工具执行有 5 秒硬上限、响应大小上限和稳定错误码；超时或无证据会停止并返回 `needs_human_review`；
- 通过依赖注入接入具体查询适配器；未配置适配器时安全返回 `BACKEND_UNAVAILABLE`，不伪造证据；
- 每次授权/执行结果输出 `agent.tool.audit` 结构化审计事件，参数只记录脱敏值和摘要。

实现证据：`backend/django_backend/deepseek_project/agent_workflow/core.py`、`backend/django_backend/deepseek_project/tests/test_agent_workflow.py`，定向测试 6/6 通过。

尚未完成：生产日志/指标/发布/依赖/事故系统适配器、审计保留与集中检索、真实下游故障演练和端到端 Agent 决策评测。当前已提供 `InMemoryReadOnlyDataSource` 作为可重复评测适配器；固定 Workflow 当前不允许模型直接改变计划、权限或工具身份。

## 10. 离线评测入口

下一轮可直接执行以下命令进行固定 Workflow 安全与契约评测：

```bash
uv run --project backend/django_backend \
  python evaluation/m8/run_workflow_evaluation.py \
  --output evaluation/m8/evidence/workflow_offline.json
```

当前固定集共 50 个用例，包含 40 个五类只读工具正常任务和 10 个越权/非法参数任务。2026-09-04 实测 50/50 通过，耗时约 0.01 秒。该结果是离线契约/安全基线，不代表真实日志、指标或发布系统可用。
