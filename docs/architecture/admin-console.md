# 运营可观测后台（M8）架构

**建立：** 2026-11-10
**状态：** 设计定稿，待实施（M8）
**关系：** 本文件是 M8 的架构事实源。任务边界与验收以 [`docs/milestones/T-M8-admin-console-plan.md`](../milestones/T-M8-admin-console-plan.md) 为准。
**上游：** `ADR-006`（DSH 参考实现与审批四值）、`ADR-008`（跨设备正文按需重建）、`docs/architecture/ai-agent-architecture.md` §6.2（事件类型全集）、`docs/APP-CONTEXT.md` §4.6（日志与监控口径）

---

## 1. 定位与非目标

**定位：AI 交互回放台。** 供技术与运维按「用户 → 会话 → 轮次 → 事件」四级下钻，回答三类问题：

1. 某个用户某次对话，模型到底看到了什么、调了哪个工具、工具返回了什么。
2. 这一轮为什么失败/中断/没有按预期行动。
3. 某个 `request_id` 对应哪次交互、花了多少 token。

**与非目标的边界：**

| 不做 | 理由 |
|---|---|
| 替代 Grafana / Prometheus / OTel | 曲线与告警看的是时间序列，本后台看的是**行**。两者受众与查询形状不同，不合并。生产当前 `ENABLE_TELEMETRY: "false"`（`docker-compose.prod.yml:57`），本后台**不依赖**它。 |
| 做业务数据 CRUD | 后台改业务行会绕过同步的 checksum / 版本键，导致客户端整批 push 毒丸。P0 一律只读。 |
| 面向用户的客服工单系统 | 本后台只解决「技术/运维定位问题」。 |
| 替代客户端会话历史 | 客户端投影 B 仍是用户可见的唯一入口；本后台读服务端原始事件。 |

---

## 2. 数据面

### 2.1 四级模型

```
User (users)
 └─ Conversation (ai_conversations)          ← 会话索引：title / last_active_at / book_id
     └─ Turn (ai_conversation_events.turn)   ← 一号一轮，一輪的所有事件共用
         └─ Event (ai_conversation_events)   ← seq 会话内单调递增
```

- `seq`：会话内唯一索引 `ix_ai_conversation_events_conv_seq`（`server/src/models/ai.py:146-151`），回放与分页的唯一游标。
- `turn`：轮号，窗口截断与保留期分组的唯一依据（`ai.py:161-163`）。
- `time`：事件发生时刻，用于算轮内耗时；`created_at` 是事务时刻，二者不等价（`ai.py:122-127`）。

### 2.2 现有能力盘点

| 事件 | 生产者 | 回放可见 |
|---|---|---|
| `user` | ✅ `TurnWriter` | 用户原话，含 loop 中途插入的输入 |
| `assistant` | ✅ `TurnWriter` | 回复 + `reasoning_content` + `tool_calls` |
| `tool` | ✅ `TurnWriter` | 工具结果，与 `tool_calls` 同批落库（配对组） |
| `quota` | ✅ `record_turn` | provider / model / prompt_tokens / completion_tokens / tokens |
| `error` | ✅ `stream._record_failure` | 安全错误码 + 固定文案（已脱敏） |
| `replace` | ✅ `summarize_overflow` | 摘要替换位置（`summary` / `upto_turn` / `turns`） |
| `approval_request` | ❌ **无生产者** | —— |
| `approval_result` | ❌ **无生产者** | —— |
| `device_request` | ⛔ 刻意不接（ADR-010 决策 5） | 由配对的 tool 结果留档 |
| `security` | ⛔ 刻意不接（04d） | —— |

**结论：一轮的 token 与模型信息在事件表内闭环**（`quota` 与消息同轮），回放不需要 JOIN `ai_usage`。`ai_usage` 只用于跨会话的聚合统计与 `request_id` 反查。

### 2.3 与 APP-CONTEXT §4.6 的口径裁决

`docs/APP-CONTEXT.md:255` 现写「禁止记录 token、原始票据、**完整对话**和账本明细」。而 `ai_conversation_events.data` **已经保存完整对话**（ADR-006 的事件日志设计，T-M7-04a 起落地）。这是既有文档与实现的漂移，M8 必须裁决，否则后台无从设计：

| 面 | 裁决 |
|---|---|
| 应用日志 / Loki | **维持禁止**：完整对话、prompt/response 正文、账本明细都不进 `logger`，不导出 OTLP。现行 `_record_failure` 只写安全码与固定文案，口径不变。 |
| 会话事件日志（PG） | **允许保存正文**，因为它是产品功能的一部分（断连恢复、分叉、跨设备重建）。访问面仅限：用户本人（经投影 API）与管理员（经 admin API）。 |
| 管理后台 | 可按权限读取原文，但受 §7 的访问审计、脱敏与导出限制约束。 |

对应地，APP-CONTEXT §4.6 的措辞需要改为「禁止把完整对话写入应用日志与日志系统；会话正文仅存于事件日志，受保留期与访问审计约束」。这一条由 T-M8-01 落地。

### 2.4 保留期与删除语义（回放的重要前提）

- 事件按 `AI_CONVERSATION_RETENTION_DAYS` 整轮清理（`server/src/modules/ai/retention.py`）。
- 删除会话 = 归档行 + **删事件正文**（`ConversationService.delete`）。
- 因此后台看到的是「保留期内的全量」，不是永久档案。回放页必须给出「该轮已超出保留期」的明确空态。

---

## 3. 会话检索设计

### 3.1 需求

按 **用户 ID + 会话关键词 + 时间区间** 定位一次会话，并支持不指定用户时的全局关键词检索（运维场景：只记得一句话，不知道是谁）。

### 3.2 方案对比

| 方案 | 做法 | 评价 |
|---|---|---|
| A 扫事件表 | `EXISTS (SELECT 1 FROM events WHERE conversation_id=… AND data::text ILIKE '%kw%')` | 零迁移，但 JSON 转 text 全扫，且不能直接命中所属会话行 |
| **B 会话级冗余 `search_text`** | `ai_conversations.search_text` 累积该会话的用户与助手文本 | **采用**：一次查询只扫命中用户的会话行；列表可直接高亮命中词；写入单点维护 |
| C 事件级生成列 + GIN | 事件表生成列 + `pg_trgm` | 能精确到轮次，但 SQLite 测试后端不支持生成列，且写入放大 |
| D 外部检索引擎 | Meilisearch / ES | 组件成本过高，与「少组件、可离线部署」原则冲突 |

### 3.3 `search_text` 列设计

新增迁移（M8-03）：

```
ai_conversations.search_text  text  NOT NULL DEFAULT ''
```

**维护点（唯一）：** `ConversationService.append_events` 在写入 Surface 事件时，把 `user` / `assistant` 的 `content` 追加进 `search_text`（与事件同事务）。`tool` 结果不进检索面——那是 JSON 结构，不是人话。

**上限与预算：**

- 单会话 `search_text` 上限 `SEARCH_TEXT_MAX_CHARS = 4096`（**待实测调整**）。超出后**保留尾部**（最近活动比首轮更可能被检索），并在列首留一个截断标记。
- 容量估算：1 万会话 × 4 K 字符 ≈ 40 MB（中文 UTF-8 约 3 字节/字），可接受。生产实测后再定。

**清理（必须与正文同步）：** `purge_expired_events` 删除事件后，必须按剩余事件**重算** `search_text`。理由与 T-M7-04d 的「摘要必须跟正文一起被清掉」同源——留着就等于给保留期开后门（该教训已记为 R-PROD 系列）。重算频率为每轮清理一次，按会话聚合，成本可控。

**回填：** 迁移后跑一次脚本，从 `ai_conversation_events` 聚合出历史会话的 `search_text`（只取 `user` / `assistant` 且未超保留期的行）。

### 3.4 索引与中文关键词

**P0 不加 `pg_trgm` 索引。** 理由：

- 检索几乎总是带 `user_id`（用户需求原文即「按用户 id + 关键词」）。已有索引 `ix_ai_conversations_user_archived_active (user_id, archived_at, last_active_at)` 能把候选集压到单个用户的几十个会话，`search_text ILIKE '%kw%'` 在此规模下是毫秒级。
- 全局关键词检索（不带 `user_id`）在内测数据量下同样可接受；等海量后再评估 GIN。

**中文 trigram 陷阱（记为待实测项）：** `pg_trgm` 按 3 字符滑窗建索引，**中文两字词（如「外卖」）不会命中 trigram 索引**，会退化为顺序扫描。若将来引入 GIN，必须同时保留短词降级路径。此项不阻塞 P0。

### 3.5 检索语义

| 条件 | 实现 |
|---|---|
| `user_id` | 精确匹配；支持从手机号 / 昵称先解析出 id |
| `q` | `search_text ILIKE '%q%'`；`q` 长度 < 2 时拒答并提示（避免无意义全扫） |
| 时间 | `last_active_at` 与 `created_at` 二选一或区间，默认 `last_active_at` |
| 排序 | `last_active_at DESC`（与客户端会话列表同序） |
| 分页 | keyset：`(last_active_at, id) < (:cursor_ts, :cursor_id)`；禁止 offset |
| 已归档 | 默认排除，运维可显式勾选 |

**返回值：** 会话行摘要（id / user_id / title / book_id / created_at / last_active_at / 命中片段 ±40 字 / 事件数 / 最近轮次）。**不返回事件正文。**

---

## 4. 审批留档设计

### 4.1 现状与缺口

`approval_request` / `approval_result` 只在类型全集里定义，**全仓无写入点**（`conversation.py:200-201` 标 `[待接]`）。后果：回放里能看到模型调了写工具，却看不到「用户是否同意、为什么问」。

审批四值（ADR-006）`allowed-once` / `rejected` / `cancelled` / `unavailable` 的观测难度不同：

| 值 | 服务端可观测？ | 依据 |
|---|---|---|
| `allowed-once` | ✅ 能 | 客户端会调 `/ai/execute`，服务端知道「同意了」 |
| `rejected` | ❌ 不能 | 用户在卡上拒绝，无任何上行请求 |
| `cancelled` | ❌ 不能 | 客户端本地中止（`ai.cancel` 只作用于流，不针对卡） |
| `unavailable` | ❌ 不能 | 无法取得授权（超时/断连/界面不可用），纯客户端状态 |

### 4.2 `approval_request`：纯服务端，零客户端改动

在 `stream.py` 构造并发送 `confirm` 帧的位置（`server/src/modules/ai/stream.py:468` 附近）写一条结构性事件，`same_turn=True`：

```json
{
  "proposalId": "…",
  "op": "add_tx",
  "risk": "medium",
  "confirmReason": "amount_over_threshold",
  "step": 2
}
```

**不写入完整 `proposal` 信封**：payload 可能很大，且与 `done.proposals` 重复；审计需要的是「问没问、为什么问、问的是哪个提案」。完整提案可通过 `proposalId` 在执行链路里查到。

### 4.3 `approval_result`：需要客户端上行

服务端仅在 `/ai/execute` 成功时能确认 `allowed-once`。要让四值全部落档，**必须新增一条客户端上行**：

```
POST /ai/approval-result
{ "proposalId": "…", "conversationId": "…", "result": "allowed-once|rejected|cancelled|unavailable", "reason": "可选，固定枚举" }
```

- 客户端在确认卡收敛到终态时调用一次（四个值都发）。
- 幂等键 `(user_id, proposal_id)`：重复上报覆盖为最后一次，不追加多行。
- 服务端校验：`conversation_id` 必须属于当前用户；`proposal_id` 必须出现在该会话的未决提案里（`approval_request` 事件可查）。校验失败仍接受写入但标记 `unverified`（审计以客户端自报为准，属内部工具可接受）。
- **桥命令计数不变**：WebView 直接 `fetch`，不走 `AozoraBridge`。
- **不改 `/ai/execute` 契约**：`AIExecuteProposalRequest`（`schemas.py:115-130`）一字不动，老客户端行为不变。

**降级：** 客户端未升级时三条非放行值缺席，后台在轮次上标「审批结果未上报」，不得据此判定用户拒绝。

### 4.4 `proposal_id → conversation_id` 关联

`/ai/execute` 请求体不含 `conversation_id`，因此 `allowed-once` 的落点需要关联。两种实现：

| 方案 | 做法 | 取舍 |
|---|---|---|
| **A 小索引表（采用）** | 写 `approval_request` 时同事务落 `ai_proposal_links(user_id, proposal_id, conversation_id, turn, created_at)`，主键 `(user_id, proposal_id)` | 查询 O(1)；表很小；`/ai/execute` 无需改契约 |
| B 提案信封加 `conversationId` | 改 v2 契约与客户端透传 | L3 契约变更，收益不抵成本 |

`ai_proposal_links` 随会话删除级联清理；保留期与事件一致。

---

## 5. API 契约（只读为主）

前缀：`{API_PREFIX}/admin`（默认 `/api/v1/admin`）。

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/admin/auth/login` | 管理员登录，返回独立 audience 的 JWT |
| GET | `/admin/users` | 用户检索（q=手机号/昵称/ID，status，时间区间） |
| GET | `/admin/users/{id}` | 用户详情（状态、注册时间、设备数、会话数、最近活动） |
| GET | `/admin/users/{id}/conversations` | 该用户会话列表（分页） |
| GET | `/admin/conversations` | 全局会话检索（q + 时间 + user_id 可选） |
| GET | `/admin/conversations/{id}` | 会话详情（元信息 + 轮次摘要） |
| GET | `/admin/conversations/{id}/turns` | 轮次摘要列表：turn / 首条用户输入 / model / tokens / 是否报错 / 事件数 / 起止时间 |
| GET | `/admin/conversations/{id}/events` | 原始事件（`after_seq` / `turn` 过滤），返回完整 `data` |
| GET | `/admin/ai-usage/summary` | 聚合：按 user / model / day 分组 |
| GET | `/admin/requests/{request_id}` | 反查：`ai_usage` → conversation → 时间窗内 turn |
| POST | `/ai/approval-result` | **非 admin 前缀**：客户端审批结果上行（§4.3） |

**通用约束：**

- 全部 `GET` 使用**只读事务**（`SET TRANSACTION READ ONLY`），从物理上排除误写。
- 不复用业务 service（`ConversationService` 等有副作用：改写 `last_active_at`、触发投影、归档）。新写 `queries.py`，只 `select`。
- 列表端点不返回事件正文；正文只在 `/events` 返回。
- 错误码复用全局 `AppException` 形状，不新增体系。

---

## 6. 鉴权与部署

### 6.1 管理员身份

新建 `admin_accounts`：`id / username / password_hash / status / created_at / last_login_at`。

- 独立 JWT：单独 secret（`ADMIN_JWT_SECRET`）与单独 `aud`（`aozora-admin`）。用户端 token **不能**进 admin API，反之亦然。
- 不复用 `users` 表、不给 `users` 加 `is_admin`——用户 token 一旦泄露就等于后台失守。
- 登录接口独立限流，不复用 C 端的 `check_auth_rate_limit`（`auth/dependencies.py:78` 的配额是按 C 端流量设计的）。

### 6.2 前端与部署

- 独立 `admin/` 包（自有 `package.json` / Vite），**不进** `app/`：不污染 APK 包体、移动端设计令牌与 WebView CSP。
- nginx：独立子域或 `location /admin/`，静态产物 + 反代 `/aozora-api/`。
- 后台页面单独放开 CSP（`server/src/common/security.py:23` 的 `default-src 'self'` 会拦 iframe；P0 不嵌 Grafana，暂不需要放开）。

---

## 7. 隐私、审计与风险

| 项 | 措施 |
|---|---|
| 列表页 PII | 手机号默认打码（`138****1234`）；事件正文不在列表返回 |
| 正文访问 | 每次拉取 `/events` 写一条 `admin_access_log`（admin_id / target_user_id / conversation_id / ip / 时间）。**新表，独立于业务表** |
| 导出 | P0 禁止批量导出；单会话 JSON 复制另行评估 |
| 保留期 | 后台展示与事件一致，超出保留期即空态；`search_text` 与事件同步清理 |
| 误写 | 只读事务 + 不接业务 service |
| 越权 | 管理员可读全部用户，但不可改；写能力（冻结/调配额）留到 M9 或后续独立卡，且必须走审计 |

**需要登记的已知风险（交付时同步 `docs/risk-register.md`）：**

1. `search_text` 含用户原话，属 PII 冗余副本；清理若漏，等于保留期失效。
2. 审批四值依赖客户端上报，老版本会出现「结果未上报」的空白。
3. `request_id` 反查是时间窗匹配，同一会话并发请求时可能命中相邻轮次（`ai_conversation_events` 无 `request_id` 列）。
4. 后台是单点高价值目标：独立 audience、独立限流、独立审计缺一不可。

---

## 8. 待实测 / 待确认项

| 项 | 何时定 | 判据 |
|---|---|---|
| `SEARCH_TEXT_MAX_CHARS` 取值 | M8-03 | 生产会话长度分布实测 |
| 全局关键词检索是否上 `pg_trgm` | M8-03 之后 | 会话总量与 P95 查询延迟 |
| `ai_conversation_events` 是否补 `request_id` 列 | 若反查误差不可接受 | M8-07 走查结果 |
| 管理员是否需要多账号 / 角色 | M8-04 开工前 | 用户确认 |
| 写能力（冻结/调配额）是否纳入 M8 | M8 收口时 | 用户确认 |
