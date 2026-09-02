# M6：前端性能实现报告

更新时间：2026-09-02

本文记录 M6“前端性能”子任务的实现、验证结果和边界。本次不包含最终答案/检索 Redis 缓存、真实设备 Web Vitals 采集和生产网络压测。

## 1. 为什么需要实现

原前端一次性加载全部会话历史，对消息数组做深度 Watch，并在每个流式 Token 到达时更新组件和重新解析 Markdown。长会话和慢模型下，这些操作会放大 DOM、响应式追踪和渲染开销；初始化、历史加载和发送生成也共用一个 Loading 状态，导致互相阻塞或展示错误。

## 2. 项目如何实现

### 2.1 长会话分页

后端历史接口增加 `latest=true` 查询语义：先按最新记录取一页，再按时间和 ID 升序返回，继续使用已有 `before_cursor` 获取更早页面。前端 `chat` Store：

- 初次进入会话只加载最新 100 条 History；
- 顶部显示“Load older messages”，按游标追加更早消息；
- 以消息稳定 ID 去重，避免重试或分页边界造成重复 DOM；
- 保留 `has_more_before` 和 `next_before_cursor`，没有更多数据时不再请求；
- 后端仍保留默认历史接口和旧的 `before_id/after_id` 兼容语义。

因此长会话的首屏不再把全部历史转换成前端消息数组，1000 条历史可分成多个小页加载。

### 2.2 最小依赖 Watch 与批量流式渲染

`ChatArea.vue` 移除消息数组深度 Watch，改为只观察当前会话、数组长度、最后一条消息的 ID/内容/streaming 状态。Store 收到 `delta` 后先放入内存缓冲，再通过 `requestAnimationFrame` 合并更新；不支持 RAF 的测试环境回退到零延迟定时器。

`done` 事件会先冲刷待处理 delta，再以最终结构化 Markdown 替换预览，保证协议结果优先且不会遗留未刷新的 Token。

### 2.3 Loading、取消和重试

`app` Store 将状态拆为 `isInitializing`、`isLoadingHistory`、`isSending` 和兼容用的 `globalLoading`；`loading` getter 只作为聚合状态，聊天组件按具体任务使用细粒度状态：

- 初始化只影响初始化提示；
- 历史翻页只影响历史按钮；
- 模型生成只显示 typing 和发送按钮 Loading；
- AbortController 取消仍移除未完成助手消息并保留用户消息；
- 非取消失败把用户消息标记为 `retryable`，Retry 复用原消息 ID，不重复插入用户消息。

### 2.4 Markdown 结果复用

新增 `src/utils/markdown.js`，全局复用一个 MarkdownIt/Highlight.js 实例。完成的助手消息使用最多 100 项的 LRU 结果缓存，同一 Markdown 源文本命中缓存时不再重复解析；流式中的不完整预览不写入完成结果缓存。Markdown 继续关闭原始 HTML，保持既有 XSS 防护。

## 3. 交互与数据流

```text
进入会话
  ↓
加载最新 100 条 ── has_more_before ──→ 点击加载更早页
  ↓                                  （游标 + 去重 + 前置插入）
发送请求
  ↓
Token 缓冲 ── requestAnimationFrame ──→ 批量更新最后一条消息
  ↓
done → 冲刷缓冲 → 最终 Markdown → LRU 复用
```

## 4. 验证结果

前端全量单元测试：

```bash
npm run test --prefix frontend/vue_frontend -- --run
```

结果：8 个测试文件、23/23 通过。新增回归覆盖：

- 最新页加载和更早 History 游标分页；
- 分页消息稳定 ID 去重和前置合并；
- 失败消息 Retry 不重复用户消息；
- 完成 Markdown 结果缓存命中。

静态与构建验证：

- `npm run lint --prefix frontend/vue_frontend`：通过；
- `npm run format:check --prefix frontend/vue_frontend`：通过；
- `npm run build --prefix frontend/vue_frontend`：通过；
- 当前生产 Bundle 基线记录：JS 2580.41 kB（gzip 779.46 kB），CSS 7.60 kB（gzip 1.99 kB）。这是本轮首次记录，尚无历史基线可作下降比较；构建提示仍建议后续做代码分包。

## 5. 当前验收结论

本子任务的分页、最小响应式依赖、流式批量渲染、Loading 拆分、取消/重试和完成 Markdown 复用已实现并有自动化证据。M6 的“长会话 1000 条消息滚动 ≤50ms”、真实设备 Web Vitals、Bundle 历史对比和端到端网络性能仍需在固定浏览器/设备条件下补测。

## 6. 边界与后续改进

1. 当前是游标分页，不是虚拟列表；单页仍会渲染 100 条消息。若单条 Markdown 极长，应继续评估动态高度虚拟列表。
2. Markdown 缓存按文本内容复用；同文不同主题/高亮配置目前不共存，主题变化时应增加渲染配置版本到缓存键或主动清理。
3. Bundle 中 Highlight.js 和 Naive UI 仍占较大体积；后续可按语言注册高亮器、路由懒加载并建立 CI Bundle 预算。
4. 当前 Retry 处理的是已返回的非取消失败；断网重连、指数退避和跨页面重试队列属于后续可靠性设计。
