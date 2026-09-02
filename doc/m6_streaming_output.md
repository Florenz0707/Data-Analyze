# M6 流式输出实现报告

更新时间：2026-09-01
范围：M6“流式输出”子任务；缓存击穿、Redis、模型健康检查和完整性能压测不在本次范围内。

## 1. 为什么需要流式输出

原聊天接口只有一个完整 JSON 响应。模型生成期间前端只能显示全局 Loading，用户感知延迟等于完整生成时间，也无法可靠地区分“正在生成”“正常完成”“模型失败”和“用户取消”。

本次改造增加独立的 SSE 聊天接口，同时保留原 `/api/llm/chat` 非流式接口作为兼容和降级路径。流式接口不把中间内容写入 History，只有最终结构化结果通过校验并发出 `done` 后才提交会话历史。

## 2. SSE 协议

接口：`POST /api/llm/chat/stream`
请求体与普通聊天接口一致，并支持可选 `message_id` 幂等键。

响应头：

```text
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

每个事件包含事件名和 JSON 数据。事件顺序为 `start`，零个或多个 `delta`，最后是 `done` 或 `error`。

### 2.1 start

```text
event: start
data: {"type":"start","session_id":"session-1","message_id":"..."}
```

表示请求已通过认证和参数校验，并确定了本次会话及幂等消息 ID。

### 2.2 delta

```text
event: delta
data: {"type":"delta","text":"# 问题诊断\n1. ..."}
```

Provider 的流式 Token 会先在后端累积为不完整 JSON。后端只从已经闭合且可识别的结构化字段提取人类可读预览，因此前端不会直接展示模型原始 JSON。预览不参与落库，也不代表最终结论。

### 2.3 done

```text
event: done
data: {"type":"done","reply":"# 问题诊断\n...","cached":false,"message_id":"..."}
```

`reply` 是经过结构化 Schema、Evidence ID 校验和 Markdown 渲染的最终答案。收到该事件后，前端用它替换所有预览内容。

### 2.4 error

```text
event: error
data: {"type":"error","code":"MODEL_UNAVAILABLE","error":"模型服务暂不可用，请稍后重试"}
```

错误沿用 M2/M3 的稳定错误码。Provider 异常不会把密钥、完整 Prompt、堆栈或原始响应写入事件；模型失败和内部错误分别返回 `MODEL_UNAVAILABLE`、`INTERNAL_ERROR`。

## 3. 后端实现

### 3.1 模型流和结构化校验

- `TopKLogSystem.stream_query()` 复用现有检索、Prompt 和用户模型实例；
- 优先调用 LangChain Provider 的 `stream()`，不支持流式的旧适配器退回一次性调用；
- `stream_response()` 对 Token 进行有限预览提取，完整输出结束后调用既有 `parse_answer()`；
- Schema 失败时仍使用已有的一次有限修复；修复后仍失败才进入兼容 Sanitizer 降级；
- 只有最终结果生成 `done`，接口层才会写缓存和 History。

实现位置：

- `backend/django_backend/topklogsystem.py`
- `backend/django_backend/deepseek_api/services.py`
- `backend/django_backend/deepseek_api/api.py`
- `backend/django_backend/deepseek_api/streaming.py`

### 3.2 一致性、取消和资源释放

流式请求在生成器内部开启事务并锁定当前 Session，与普通聊天保持同一会话串行写入语义：

```text
认证/限流
  ↓
锁定或创建 Session
  ↓
发送 start，开始模型 stream
  ↓
发送 delta（仅预览）
  ↓
完整输出 → Schema/Evidence 校验
  ├─ 失败 → 回滚并发送 error
  └─ 成功 → 写缓存、写 History、发送 done
```

客户端使用 `AbortController` 取消请求。服务端检测到 `GeneratorExit` 后退出事务，关闭 Provider stream，不提交部分回答；因此断线、取消和模型异常都不会留下伪完整 History。已有 `message_id` 重试仍返回之前的完整答案，不重复写入历史。

缓存命中时不调用模型，直接发送完整答案作为一个 `delta`，随后发送 `done`；最终 History 的提交语义与普通请求一致。

## 4. 前端实现

- `src/api/llm.js` 使用 `fetch` 读取 SSE，支持跨网络分片、UTF-8 解码和结构化错误；
- 流式请求携带当前 Access Token、Refresh Cookie，并在 401 时复用一次 Refresh Token 刷新；
- `chat` Store 为用户消息和助手消息生成稳定 ID；
- 助手消息先创建为空的 `streaming` 占位，`delta` 追加预览，`done` 替换为最终 Markdown；
- `cancelGeneration()` 调用 AbortController，移除未完成助手消息并保留用户消息；
- `ChatArea.vue` 使用消息 ID 作为 Vue Key；
- 输入区在生成期间显示取消按钮，避免重复发送。

实现位置：

- `frontend/vue_frontend/src/api/llm.js`
- `frontend/vue_frontend/src/stores/chat.js`
- `frontend/vue_frontend/src/components/MessageInput.vue`
- `frontend/vue_frontend/src/components/ChatArea.vue`

## 5. 测试证据

### 5.1 后端

命令：

```bash
DJANGO_TESTING=true \
DJANGO_DB_CONFIG=config/db_config.yaml.example \
uv run --project . python manage.py test \
  deepseek_project.tests.test_topk_generation_contract \
  deepseek_api.tests.test_streaming \
  deepseek_api.tests.test_api
```

结果：44/44 通过，使用 Git 跟踪的 SQLite 配置模板和临时测试数据库。

覆盖内容：

- SSE 事件编码、分片解析和中文保真；
- Provider 分片流、预览 delta 和最终结构化 done；
- 无证据时不调用模型并返回确定性拒答；
- 完成事件前不写 History；
- 客户端断开后不保留部分 History；
- 未认证流式接口仍受认证、限流和参数校验保护。

### 5.2 前端

命令：

```bash
npm run test --prefix frontend/vue_frontend -- --run
```

结果：8 个测试文件、20 个测试通过。

覆盖内容：

- SSE 分片读取和 `start/delta/done` 事件分发；
- 非 2xx 响应转换为统一错误对象；
- 流式消息稳定 ID；
- 取消后移除未完成助手消息并恢复 Loading 状态。

## 6. 当前验收结论

已完成并有自动化证据的 M6 流式子项：

- 模型调用支持 Provider `stream()`，旧适配器有兼容回退；
- 后端 SSE 事件协议；
- `start/delta/done/error` 事件定义；
- 前端增量更新和批量式响应状态切换；
- 用户取消、Provider stream 关闭和事务回滚；
- 断开连接不保存伪完整回答；
- `done` 后才提交最终 History；
- 最终回答仍经过结构化 Schema 和证据 ID 校验。

M6 总体仍不能标记完成。以下指标尚未实测或不属于本次实现：云端首个流式事件 P95、用户取消生效 P95、20 并发错误率/模型串台、缓存命中 P95、同 Key 缓存击穿、真实部署下的连接池复用和长会话滚动性能。

## 7. 后续改进

1. 在固定外部模型、输入输出 Token、网络和并发条件下，至少重复 3 次测量首个 `delta`、`done`、取消生效和错误率，记录 P50/P95/P99。
2. 在 ASGI 或实际 WSGI 多 worker 部署下验证客户端断开是否及时触发生成器关闭；若 Provider 不响应关闭，需要增加可取消的请求句柄和超时预算。
3. 将长时间持有 Session 行锁改为显式生成任务状态或短事务协调，避免慢模型阻塞同会话的只读请求；改造后必须重新验证并发写入和幂等。
4. 对最终答案和检索结果分别设计缓存协议，再加入同 Key 请求合并；流式缓存命中必须保持与模型生成相同的事件顺序。
5. 通过反向代理验证 SSE 禁止缓冲、心跳、空闲超时和断线重连策略，并确保重连使用 `message_id` 而不是重复提交新消息。
