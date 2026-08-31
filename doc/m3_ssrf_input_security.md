# M3 SSRF 与输入安全实现报告

## 1. 风险

外部模型配置中的 `base_url` 会触发服务端主动连接。如果只校验字符串格式，攻击者可以利用环回、内网、链路本地或云元数据地址读取内部服务；如果先解析域名再由 HTTP 客户端重新解析，还可能通过 DNS 重绑定绕过预检。重定向、无限响应和无界超时也会形成内部探测、资源耗尽或请求阻塞风险。

## 2. 防护策略

- 只接受 `http`/`https`；默认只允许 HTTPS，HTTP 仅在显式设置 `ALLOW_INSECURE_EXTERNAL_HTTP=true` 时可用。
- 拒绝用户名、密码、查询参数和片段，限制 URL 长度为 512 字符。
- 对 IP 字面量和 DNS 全部解析结果执行公网校验，拒绝环回、私网、链路本地、组播、保留、未指定、文档地址和云元数据地址；IPv4-mapped IPv6 也会被识别。
- 校验后把公网 IP 固定到自定义 httpcore 网络后端，实际 TCP 连接不再重新按域名解析；TLS 仍使用原始主机名完成证书校验。
- `trust_env=false`，不读取环境代理，避免代理改变实际访问边界。
- 自动重定向关闭，最大重定向数固定为 0；若响应带 `Location`，仍校验目标，危险目标直接拒绝。
- 使用独立的连接、读取、写入和连接池超时；响应 `Content-Length` 和流式读取总量均受 `EXTERNAL_API_MAX_RESPONSE_BYTES` 限制。

同一安全 HTTP 客户端同时用于连通性探测和外部模型生成，避免只保护保存时探测、却在后续聊天路径绕过校验。

## 3. 配置

```text
ALLOW_INSECURE_EXTERNAL_HTTP=false
EXTERNAL_API_CONNECT_TIMEOUT_SECONDS=5
EXTERNAL_API_READ_TIMEOUT_SECONDS=10
EXTERNAL_API_WRITE_TIMEOUT_SECONDS=5
EXTERNAL_API_POOL_TIMEOUT_SECONDS=5
EXTERNAL_API_MAX_RESPONSE_BYTES=1048576
EXTERNAL_API_MAX_REDIRECTS=0
```

开发环境如需访问本机模拟服务，应使用明确的测试替身或隔离网络；不要在生产环境打开 HTTP 例外，也不要把 `RATE_LIMIT_TRUST_PROXY` 等代理信任配置当作 SSRF 防护替代品。

## 4. 输入与错误语义

外部模型保存接口在发起探测前执行 URL 安全校验。格式错误、非 HTTPS、凭据 URL、非公网地址和无法解析的域名返回 `400 VALIDATION_ERROR`，不会保存配置，也不会发起 Provider 探测。通过安全校验但 Provider 不可用时仍返回 `503 MODEL_UNAVAILABLE`。生成阶段对历史不安全记录再次校验，阻断后不会请求危险地址。

## 5. 验收结果

- SSRF 安全测试覆盖协议、内网/环回/元数据 IP、IPv4-mapped IPv6、凭据/查询/片段、混合 DNS 结果、重定向目标和响应大小限制。
- 外部模型 API 测试覆盖危险地址在探测前拦截，且日志不包含 API Key。
- 后端全量测试：83/83 通过。
- `makemigrations --check --dry-run`：无模型变化；本任务不新增迁移。
- 未访问真实外部服务；网络测试使用公网 IP 字面量、Mock Provider 和 Mock Transport。

## 6. 边界与后续改进

当前策略将重定向完全关闭，安全性优先于兼容需要重定向的 Provider。生产还应配合出口防火墙、DNS 监控、KMS、审计和 API Gateway；若未来需要允许重定向，应逐跳解析、校验并固定目标 IP，同时限制次数、跨主机策略和响应预算。
