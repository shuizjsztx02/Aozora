# ADR-003 WebView AI 调用通道

**状态：** 已采纳（2026-09-06）
**范围：** T-M4-01d、T-M4-02c、T-M4-03c、T-M4-04e

## 背景

生产仓库前端为 WebView（T-M4-00a），AI 调用（`/ai/interpret`、`/ai/report`、`/ai/validate`、`/ai/execute`）必须携带 Bearer token。T-M4-00g 的验收标准明确"WebView 存储无 token"，同时架构原则（`docs/architecture/ai-architecture.md`）要求"模型密钥只存在服务端密钥管理环境；客户端不得直接调用模型提供商"。

需要一条让 WebView 调用服务端 AI 端点的通道，同时满足：
- Token 不出 Kotlin 层（T-M4-00g 验收 + `security.md`）
- 与服务端降级契约（`APP-CONTEXT §6.4`）一致：断网 / 超时 / 503 / 429 → 客户端回退本地 NLU
- 符合 12 s 慢速提示 / 25 s 取消的 SLO（`APP-CONTEXT §10.3`）

## 决策

WebView JS 通过现有 `AozoraBridge` 下发 `ai.*` 命令，Kotlin 层负责 token 注入与 HTTP 调用。Token 全程不进入 WebView 存储或 JS 内存。

| 项 | 选择 | 备注 |
|---|---|---|
| 通道 | 复用 `AozoraBridge`，新增 `ai.*` 命令族 | 不引入新 IPC 机制；版本协商复用 `BridgeProtocol.VERSION` |
| 命令 | `ai.interpret` / `ai.report` / `ai.validate` / `ai.execute` | 与服务端 `/ai/*` 路由一一对应 |
| Token 注入 | Kotlin `AuthClient.ensureValidAccessToken()` 在请求头附加 `Authorization: Bearer …` | Token 仅驻留 `TokenStore`（Keystore 加密），Kotlin 层组装后一次使用 |
| HTTP 客户端 | 复用 `AuthClient` 使用的 `OkHttpClient`，`callTimeout = 25 s` | 与服务端 25 s 取消预算对齐；超时 → Kotlin 抛 `SocketTimeoutException` → 返回桥 `timeout` 错误 |
| TS 命令超时 | `NativeBridge.call` 的 per-command 超时从默认 10 s 提升到 25 s（仅 `ai.*` 命令） | 通过 `call<T>(command, payload, { timeoutMs: 25_000 })` 形式传入（`native-bridge.ts` 需扩展签名） |
| 12 s 慢速提示 | 客户端 UI 计时器：调用起 12 s 未收到响应 → 展示"正在处理中…"文案；25 s 仍无响应 → 取消并回退本地 NLU | 服务端通过 `_log_slow_if_needed` 记录 `outcome: slow` 事件（T-M5-05b 看板消费），客户端侧无服务端可推送通道 |
| 降级映射 | Kotlin 返回 `bridge` 错误码映射表：`timeout` / `network` / `503` / `429` / `auth_lost` → TS 层统一触发 `app/src/domain/nlu/parser.ts` | 与服务端 `ai_unavailable` / `ai_rate_limited` / `ai_quota_exceeded` 契约对齐 |
| 请求体 | WebView 仅下发业务参数（`input` / `book_id` / `proposal`），Kotlin 负责附加 `X-Request-Id` | `X-Request-Id` 由 Kotlin 生成（UUID v4），确保链路追踪贯穿 |

## 调用序列

```
WebView (JS)                              Kotlin layer
    │                                          │
    │ bridge.call(v, reqId,                    │
    │   "ai.interpret",                        │
    │   { input: "午饭 12.5" })                │
    │────────────────────────────────────────▶ │
    │                                          │ token = ensureValidAccessToken()
    │                                          │ POST /ai/interpret
    │                                          │ Authorization: Bearer <token>
    │                                          │ X-Request-Id: <uuid>
    │                                          │ callTimeout: 25s
    │                                          │──────▶ Server
    │                                          │◀────── 200 AIInterpretResponse
    │◀──────────────────────────────────────── │ __aozoraBridgeRespond(json)
    │                                          │
    │ resolve Promise with response data       │
```

## 拒绝的替代方案

1. **WebView 直接 fetch 服务端 `/ai/*`** —— 需要在 JS 层持有 Bearer token，违反"T-M4-00g 验收：WebView 存储无 token"与 `security.md`。
2. **Kotlin 端调用本地模型 SDK** —— 模型密钥只允许存在服务端密钥管理环境（`docs/architecture/ai-architecture.md`）；客户端直接持有密钥违反密钥托管策略（D-EXEC-04）。
3. **独立 AI IPC 通道（ContentProvider / BroadcastReceiver）** —— 与现有 `AozoraBridge` 功能重叠，增加维护负担；现有桥已有版本协商 + 请求 ID 机制，复用成本更低。

## 后果

- ✅ Token 始终不离开 Kotlin 层（`TokenStore` + `AuthClient`）；WebView 零信任面
- ✅ 降级契约与服务端一致；断网 / 超时 / 503 / 429 任一失败路径均回退本地 NLU
- ✅ 12 s 慢速提示客户端可独立实现（UI 计时器），不依赖服务端推送能力
- ✅ 链路追踪完整：`X-Request-Id` 由 Kotlin 生成 → 服务端 `AIUsage.request_id` → 日志
- ⚠️ `NativeBridge.call` 签名需扩展支持 per-command 超时（一次性改动）
- ⚠️ Kotlin 层新增 `AiGatewayClient`（薄封装 OkHttp + AuthClient），约 80 行
- ✅ 桥版本保持 v1（`BridgeProtocol.VERSION = 1`），无需升级 v2：新增 `ai.*` 命令族属向后兼容扩展（只增命令、不改既有命令/响应契约；WebView JS 与 Kotlin 同包发布，版本恒一致），未知命令由 `AozoraBridge` 的 `unknown_command` 分支兜底，`unsupported_version` 分支保留给未来的不兼容变更
