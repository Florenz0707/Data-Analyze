# M2：模型实例隔离

更新时间：2026-08-29

本文记录 M2 中“模型实例隔离”子任务的实现、验证结果和后续改进方向。Session/History 外键、迁移和删除一致性属于 M2 的其他子任务，未包含在本次变更中。

## 1. 为什么需要实现

原实现把用户选择的 LLM 临时写入 LlamaIndex 全局 `Settings.llm`，请求结束后再恢复。这个方案在单线程演示中看似可用，但在并发请求之间存在竞态：用户 A 写入模型 A 后，用户 B 可能覆盖为模型 B，A 的生成请求就可能得到错误模型的结果。Embedding 通过全局 `Settings.embed_model` 读取，也存在相同的隐式依赖。

此外，原来的 Provider 工厂只接收 provider，没有接收用户选择的具体模型名，因此“选择模型”可能只更新数据库偏好，却没有改变实际调用模型。回复缓存若不包含模型和 endpoint，也可能把一个模型的结果复用到另一个模型。

## 2. 项目如何实现

### 2.1 显式依赖传递

`TopKLogSystem` 现在持有自己的 `self.llm` 和 `self.embedding`，索引构建和检索显式传递 `embed_model`，生成时显式传递 `llm`。`query()` 支持以下调用形式：

```python
system.query(question, llm=user_llm, embedding=user_embedding)
```

请求期间不再写入或恢复 `LlamaIndex Settings.llm` / `Settings.embed_model`。用户请求由 `generate_with_user_llm()` 将对应的 LLM 实例传入查询链路。

### 2.2 有界实例缓存

新增 `deepseek_project/model_runtime.py`：

- 使用 `ModelInstanceKey(provider, model, endpoint)` 标识实例；
- 使用线程安全的有界 LRU 风格缓存，默认每类实例最多 4 个；
- 首次构建在锁内完成，避免同一 key 并发重复加载模型；
- LLM 与 Embedding 分开缓存，避免不同模型或 endpoint 共享错误实例；
- `MODEL_CACHE_MAX_SIZE` 可在 `llm_config.yaml` 调整；
- 测试和受控配置重载可调用 `clear_model_caches()` 清理缓存。

Provider 工厂现在接收可选的 `model` 参数，并把它真正传给 Ollama、Transformers、OpenAI-compatible 和 DashScope 的对应 SDK。模型选择流程变为：

```text
用户偏好 → provider + model → ModelInstanceKey → 有界缓存 → Provider 工厂 → 显式 query(llm=...)
```

### 2.3 缓存正确性

回复缓存现在由独立的 M2 缓存正确性子任务维护：键包含用户、Session、完整 Prompt、选中历史、生成参数、Provider、模型、endpoint、Prompt/索引版本和可轮换命名空间，并使用 SHA-256 摘要。空值和错误响应不写入，TTL 默认 3600 秒；Prompt 或索引更新后可通过 `invalidate_reply_cache` 管理命令批量逻辑失效。详细契约见 `doc/m2_cache_correctness.md`。

## 3. 相关模型与组件

本次隔离的对象不是某一个具体权重，而是以下运行时实例：

| 层次               | 当前组件                                                        | 隔离要点                                                           |
| ------------------ | --------------------------------------------------------------- | ------------------------------------------------------------------ |
| LLM Provider       | Ollama、Hugging Face Transformers、OpenAI-compatible、DashScope | provider、模型名、endpoint 共同决定实例身份                        |
| Embedding Provider | Ollama、Hugging Face、OpenAI-compatible、DashScope              | 作为显式 embedding 依赖传入索引和 Retriever                        |
| LlamaIndex 适配层  | `LangChainLLM`、`LangchainEmbedding`                            | 由系统实例或请求调用链持有，不写全局 Settings                      |
| 检索层             | `VectorStoreIndex`、`VectorIndexRetriever`                      | Retriever 显式接收 embedding，避免隐式全局状态                     |
| 向量存储           | Chroma PersistentClient                                         | 通过 embedding 模型派生集合；不同维度/版本需使用不同集合或索引版本 |

这里的“隔离”包括两层含义：不同用户的请求不能互相覆盖当前 LLM；不同模型名和 endpoint 不能错误共享实例或回复缓存。实例缓存是进程内优化，不是跨进程共享状态。

## 4. 验证结果

后端测试命令：

```bash
cd backend/django_backend
DJANGO_TESTING=true uv run --project . python manage.py test --noinput
```

当前结果：57/57 通过。其中包括：

- 50 个并发访问同一模型 key 只构造一个实例；
- 不同模型和 endpoint 不共享缓存实例；
- 用户选择的模型名确实传入 Provider 工厂；
- Fake LLM 请求调用不修改全局模型状态；
- Fake Provider 使用临时 Chroma 目录，测试后自动清理；
- 全部回归测试不访问真实模型、密钥或外部网络。

## 5. 进一步改进

1. **多进程部署**：当前缓存只在单个 worker 内有效。生产部署可按 worker 预热，或建设独立模型服务；不要把模型对象直接放进 Redis。
2. **显式 Runtime 对象**：进一步把 LLM、Embedding、Retriever、索引版本和生成参数封装为不可变 `ModelRuntime`，避免调用方分别传递多个参数。
3. **模型生命周期管理**：增加命中率、构建耗时、显存/内存占用和淘汰指标；在 GPU 紧张时按显存预算淘汰，而不只按实例数量淘汰。
4. **Embedding 与索引版本**：若允许用户选择不同 Embedding，应为每个 embedding 模型/维度/数据版本建立独立集合，并在切换前校验维度。
5. **外部自定义模型**：把 `ExternalLLMAPI` 的稳定配置 ID、endpoint 和密钥引用纳入同一 runtime key；密钥应改为加密存储，日志只显示掩码。
6. **异步与取消**：为异步 Provider 增加超时、取消和并发预算，避免模型初始化或请求长期占用缓存锁。
7. **持续并发验收**：在固定 Fake Provider 下扩展到 50 个并发请求、多个用户、多个 Session，并把串台次数、缓存污染次数和模型选择一致率纳入 CI 报告。

## 6. 本次变更边界

本次仅完成模型实例和缓存隔离。M2 仍需继续处理实际限流接入和更完整的真实部署压测；这些事项不能因为本次模型隔离测试通过而提前标记完成。
