# Aozora · AI 架构

本地规则 NLU 提供离线兜底，服务端 AI Gateway 提供在线理解。模型密钥只存在服务端密钥管理环境；客户端不得直接调用模型提供商。

AI 只返回通过 JSON Schema 校验的 ActionProposal。金额、日期、权限和写库始终由确定性业务代码负责，低置信结果必须先经用户确认。

## ActionProposal 契约

- **JSON Schema：** `contracts/action-proposal/schema.json`（Draft-07，版本化 URL `$id`）
- **TypeScript 类型 + 校验器：** `app/src/domain/action-proposal.ts`
- **Fixtures：** `contracts/action-proposal/fixtures/`（valid + invalid 正反例）
- **变更规则：** 协议修改必须先更新本文件和 `contracts/action-proposal/schema.json`，并新增 ADR 或协议版本号

## interpret → validate → execute 流水线（R-M4-03 契约澄清）

`POST /ai/interpret` 返回**最小解析结果**（type/amountCents/category/note/bizDate/confidence 等中间表示），它**不是 ActionProposal**，服务端对其只做「是 JSON 对象」解析、不做提案 Schema 校验。ActionProposal 由**客户端组装**，随后必须通过 `POST /ai/validate`（JSON Schema Draft-07 + 账本上下文 categoryIds/accountIds）校验，方可提交 `POST /ai/execute`；执行前服务端做 TOCTOU 重校验。首段「AI 只返回通过 JSON Schema 校验的 ActionProposal」约束的是**写库路径**：任何 AI 来源的数据在写库前必经 Schema 校验，interpret 的输出本身永不直接入库。

## WebView 调用通道

客户端 WebView 经 `AozoraBridge` 的 `ai.*` 命令族调用服务端 `/ai/*`；Kotlin 层负责 token 注入，Bearer token 全程不出 Kotlin 层（详见 `docs/adr/ADR-003-webview-ai-channel.md`）。超时预算遵循 APP-CONTEXT §10.3：12 s 客户端慢速提示 / 25 s 取消并回退本地 NLU。

## /ai/report 限流降级契约

`POST /ai/report` 的限流/配额命中 **不返回 429**，而是降级为 200 + 纯事实包（`narrative=null`, `narrative_cached=false`, `usage=null`）。理由：事实包是确定性数据聚合，不产生 LLM 成本；客户端只需一条降级路径（narrative 缺失 → 本地展示纯数据卡片）。`/ai/interpret` 仍保留 429 行为（纯 LLM 端点，无事实包可降级返回）。

### T-M1-05 v1 约束修复

- 未知字段必须拒绝；金额上限为 9007199254740991 分；出现 transactions 时必须非空。
- bizDate 的 `format: date` 必须以断言模式验证完整公历日期，不能只检查正则或把 format 当注释。此为既有 v1 约束修复，不新增动作或改变合法提案字段。
- `validateProposal(input)` 仅作结构校验；执行前传入 `ProposalValidationContext`（当前账本允许的 categoryIds/accountIds），拒绝未解析分类和未授权 ID。上下文来自可信 repository/API，不能取自模型输出。M2/M4 接线前不能把结构通过当作执行许可。

## 会话管理端点（T-M7-03a）

`/ai/agent` 的会话由**服务端持有**（`ai-agent-architecture.md` §6.3），P2 因此补四个管理端点：

| 端点 | 作用 | 关键口径 |
|---|---|---|
| `GET /ai/conversations` | 本人的会话列表（未归档，按活跃时间倒序） | 只回本人；不分页；`last_active_at` 一定有值（还没发言的会话用 `created_at` 兜） |
| `PATCH /ai/conversations/{id}` | 重命名 | **不动活跃时间**（改名字不该让会话在列表里跳位）；纯空白 / 超长 422 |
| `DELETE /ai/conversations/{id}` | 删除 | 行归档（`archived_at`）、事件正文删除；重试安全 |
| `GET /ai/conversations/{id}/events?after_seq=N` | 事件增量读取 | `seq` **严格大于** `after_seq`，按 `seq` 升序；原样返回（Surface 子集归 P3 的 04a） |

**归属校验：** 别人的 id、已删除的 id、不存在的 id 一律 404 `conversation_not_found`，三者不可分辨（读取面的唯一防线）。

**删除语义：** 会话行**归档**而不是物理删除（`ai_usage.conversation_id` 指着它，硬删会把审计行置空；P3 的保留期也只删事件、不删行），而事件正文**立即删除**——用户主动删除后服务端不该再持有它。

**登出粒度（2026-09-19 结论，落地 P1 挂账 1）：** 登出仍按**用户**粒度清理该用户的全部会话，**不引入设备维度**。`ai_conversations` 没有设备列、`/ai/agent` 也不带 `device_id`，加一层设备维度等于同时改请求契约与客户端桥，而 P2 不做跨设备正文同步（归 P3 的 04e 决策门）。代价（已知并接受）：多设备下 A 机登出会让 B 机的服务端会话消失，B 机下一轮带旧 id 会拿到 404——**客户端必须把它当作「该 id 已不是我的会话」，丢弃并开新会话，不得作为错误呈现**；本地投影缓存仍在，历史依然可见。
