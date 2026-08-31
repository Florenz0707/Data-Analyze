# M3 外部模型闭环实现报告

## 1. 为什么需要实现

系统原先可以接收、验证、保存和删除外部 OpenAI 兼容模型配置，但这些配置没有进入用户实际生成路径：用户选择的是字符串，生成仍读取全局默认 Provider；删除当前使用的配置也没有明确回退；API Key 还以明文存储。这会造成 UI 状态与实际调用不一致，并扩大密钥泄漏风险。

## 2. 当前数据与调用链

`UserLLMPreference.external_api` 通过外键指向 `ExternalLLMAPI`，因此别名只是展示和解析入口，真正绑定的是稳定的数据库 ID。用户选择内置 Provider 时清空该外键；选择自定义别名或模型名时，只在当前用户范围内解析并绑定。

聊天生成流程如下：

1. 读取当前用户的 `UserLLMPreference`；
2. 若存在 `external_api`，读取对应 Base URL、模型名和加密 API Key；
3. 在发送请求前解密 API Key，构造独立的 OpenAI 兼容 LLM；
4. 以 Provider、模型、Base URL 和 API Key 摘要组成实例缓存身份，密钥轮换不会复用旧客户端；
5. 将该实例显式传入 `TopKLogSystem.query`，不修改全局模型状态。

## 3. 密钥保护

`ExternalLLMAPI.api_key_encrypted` 使用 Fernet 加密。优先使用 `EXTERNAL_API_ENCRYPTION_KEY`；未配置时由部署的 `DJANGO_SECRET_KEY` 派生兼容密钥，便于开发环境迁移。生产环境必须使用高熵、稳定且不入库的密钥，并建立备份和轮换流程。

密钥只在以下边界短暂出现：写入时加密、连通性探测时传给客户端、生成时解密后传给 Provider。API 响应只返回模型别名/名称，模型的 `__str__` 和日志不输出密钥。0012 迁移会把旧 `api_key` 明文转换为密文后删除旧字段。

## 4. 管理行为

- 添加或更新：相同用户和模型名执行更新，新的 API Key 会重新加密；同一用户的别名与模型名不能冲突。
- 选择：兼容前端传入别名、模型名或 `external + model`，但查询始终限定当前认证用户。
- 生成：使用保存的 Base URL、模型名和密钥，不回退到其他用户或全局外部配置。
- 删除：若配置正在使用，先把偏好恢复到规范文件中的默认 Provider/模型，再删除配置。
- 连通性验证：仍使用最小请求，仅验证成功与否，不写入聊天 Session/History。

## 5. 迁移与配置

新增迁移：`0012_external_model_closure.py`，包含偏好外键和明文 API Key 到 Fernet 密文的转换。可配置：

```text
EXTERNAL_API_ENCRYPTION_KEY=<base64-url-safe-fernet-key>
DJANGO_SECRET_KEY=<stable-deployment-secret>
```

`EXTERNAL_API_ENCRYPTION_KEY` 变化会使旧密文无法解密；轮换时应先使用旧密钥解密并重新加密全部记录，再切换新密钥。当前迁移已通过模型检查，应用到当前 PostgreSQL 的状态应以 `showmigrations` 为准。

## 6. 验收结果

- 后端全量测试：77/77 通过。
- 覆盖用户范围解析、别名冲突、外部选择、生成构造参数、密钥加解密、更新/删除回退和响应安全边界。
- `makemigrations --check --dry-run`：无模型漂移。
- 0012 迁移：已应用到当前 PostgreSQL。
- 未调用真实外部模型；连通性和生成均使用 Mock/Fake，普通回归不依赖真实密钥或网络。

## 7. 边界与后续改进

外部 Base URL 的 SSRF 与输入安全边界已在独立的 `doc/m3_ssrf_input_security.md` 中完成；进一步还应接入 KMS/Vault、增加密钥审计和按版本轮换、将外部配置的用户字段升级为数据库 User 外键，并补充真实 Provider 的脱敏集成测试。
