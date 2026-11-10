# 运营可观测后台（M8）架构

**建立：** 2026-11-10
**状态：** 设计定稿，决策已锁定（2026-11-10 用户逐项确认），待实施
**关系：** 本文件是 M8 的架构事实源。任务边界与验收以 [`docs/milestones/T-M8-admin-console-plan.md`](../milestones/T-M8-admin-console-plan.md) 为准。
**上游：** `ADR-006`（DSH 参考实现与审批四值）、`ADR-008`（跨设备正文按需重建）、`docs/architecture/ai-agent-architecture.md` §6.2（事件类型全集）、`docs/APP-CONTEXT.md` §4.6（日志与监控口径）

---

## 1. 定位与非目标

**定位：AI 交互回放台。** 供技术与运维按「用户 → 会话 → 轮次 → 事件」四级下钻，回答三类问题：

1. 某个用户某次对话，模型到底看到了什么、调了哪个工具、工具返回了什么。
2. 这一轮为什么失败/中断/没有按预期行动。
3. 某个 `request_id` 对应哪次交互、花了多少 token。

**非目标：**

| 不做 | 理由 |
|---|---|
| 替代 Grafana / Prometheus / OTel | 曲线与告警看时间序列，本后台看**行**。生产 `ENABLE_TELEMETRY: "false"`（`docker-compose.prod.yml:57`），本后台不依赖它。 |
| 修改业务数据行 | 绕过同步 checksum/版本键会让客户端整批 push 毒丸。写能力仅限 §8 的三项账户操作。 |
| 面向用户的客服工单系统 | 本后台只解决「技术/运维定位问题」。 |
| 替代客户端会话历史 | 客户端投影 B 仍是用户可见的唯一入口。 |

---

## 2. 数据面

### 2.1 四级模型

```
User (users)
 └─ Conversation (ai_conversations)
     └─ Turn (ai_conversation_events.turn)
         └─ Event (ai_conversation_events)
```

- `seq`：会话内唯一索引 `ix_ai_conversation_events_conv_seq`（`server/src/models/ai.py:146-151`），回放与分页游标。
- `turn`：轮号，窗口截断与保留期分组的唯一依据（`ai.py:161-163`）。
- `time`：事件发生时刻；`created_at` 是事务时刻，二者不等价（`ai.py:122-127`）。
- **`request_id`（M8 新增）**：事件级请求标识，`varchar(64) NULL` + 索引。历史事件回填为 `NULL`（无法可靠追溯），新事件由 `TurnWriter` 写入。

### 2.2 现有能力盘点

| 事件 | 生产者 | 回放可见 |
|---|---|---|
| `user` | ✅ `TurnWriter` | 用户原话，含 loop 中途插入的输入 |
| `assistant` | ✅ `TurnWriter` | 回复 + `reasoning_content` + `tool_calls` |
| `tool` | ✅ `TurnWriter` | 工具结果，与 `tool_calls` 同批落库 |
| `quota` | ✅ `record_turn` | provider / model / prompt_tokens / completion_tokens / tokens |
| `error` | ✅ `stream._record_failure` | 安全错误码 + 固定文案（已脱敏） |
| `replace` | ✅ `summarize_overflow` | 摘要替换位置 |
| `approval_request` | **M8-02 新增** | 提问理由、提案标识、Step |
| `approval_result` | **M8-02 新增** | 四值 + 来源（服务端确认 / 客户端上报） |
| `device_request` | ⛔ 刻意不接（ADR-010 决策 5） | 由配对的 tool 结果留档 |
| `security` | ⛔ 刻意不接（04d） | —— |

**结论：一轮的 token 与模型信息在事件表内闭环**（`quota` 与消息同轮），回放不需要 JOIN `ai_usage`。

### 2.3 与 APP-CONTEXT §4.6 的口径裁决

`docs/APP-CONTEXT.md:255` 写「禁止记录 token、原始票据、**完整对话**和账本明细」，而 `ai_conversation_events.data` 已保存完整对话（ADR-006 的事件日志设计）。裁决：

| 面 | 结论 |
|---|---|
| 应用日志 / Loki | **维持禁止**：完整对话、prompt/response 正文、账本明细不进 `logger`，不导出 OTLP。 |
| 会话事件日志（PG） | **允许保存正文**——它是产品功能的一部分（断连恢复、分叉、跨设备重建）。访问面仅限用户本人与管理员。 |
| 管理后台 | 可按权限读取，受 §7 的脱敏、审计与导出约束。 |

APP-CONTEXT §4.6 已按此口径修订。

### 2.4 保留期与删除语义

- 事件按 `AI_CONVERSATION_RETENTION_DAYS` 整轮清理（`server/src/modules/ai/retention.py`）。
- 删除会话 = 归档行 + 删事件正文。
- **决策：超出保留期即空态**（用户确认）。后台不保留副本，展示口径与事件一致。

---

## 3. 会话检索设计

### 3.1 方案

`ai_conversations.search_text text NOT NULL DEFAULT ''`，累积该会话内 **`user` / `assistant`** 的 `content`。

- **检索范围（用户确认）：只搜用户输入 + 助手回复。** `tool` 结果与 `reasoning_content` 不进检索面。
- **维护点（唯一）：** `ConversationService.append_events` 写 Surface 事件时同事务追加。
- **上限：** `SEARCH_TEXT_MAX_CHARS = 4096`，超出保留尾部（最近活动更可能被检索），列首留截断标记。容量实测后调整。
- **清理：** `purge_expired_events` 删除事件后**重算** `search_text`，与正文同寿（与 T-M7-04d「摘要随正文清掉」同源）。

### 3.2 索引与全局搜索

**必须支持全局关键词搜索**（不指定用户，用户确认）。分两档：

| 场景 | 查询 | 索引策略 |
|---|---|---|
| 带 `user_id` | `user_id = ? AND search_text ILIKE '%q%'` | 既有 `ix_ai_conversations_user_archived_active` 已能收窄到单用户几十行，**P0 不加索引** |
| 全局（不带 `user_id`） | `search_text ILIKE '%q%' ORDER BY last_active_at DESC` | P0 顺序扫描；**数据量上来后加 `pg_trgm` GIN**，短词（<3 字符）保留顺序扫描降级 |

**中文 trigram 陷阱：** `pg_trgm` 按 3 字符滑窗，中文两字词（如「外卖」）不会命中索引。因此 GIN 只能作为「长词加速」，不能作为唯一路径。是否引入由 M8-03 之后的性能实测决定，**不阻塞 P0**。

### 3.3 检索语义

| 条件 | 实现 |
|---|---|
| `user_id` | 精确匹配；支持先由手机号/昵称解析 id |
| `q` | `ILIKE '%q%'`；`q` 长度 < 2 拒答 |
| 时间 | `last_active_at` 或 `created_at` 区间，默认 `last_active_at` |
| 排序 | `last_active_at DESC` |
| 分页 | keyset `(last_active_at, id)`，禁止 offset |
| 已归档 | 默认排除，可显式勾选 |

**列表返回值不含事件正文**，只返回会话摘要与命中片段。

---

## 4. 审批留档设计

### 4.1 四值的可观测性

| 值 | 服务端可观测？ | 来源 |
|---|---|---|
| `allowed-once` | ✅ | `/ai/execute` 成功 |
| `rejected` | ❌ | 客户端上报 |
| `cancelled` | ❌ | 客户端上报 |
| `unavailable` | ❌ | 客户端上报 |

**决策：接受客户端增加一次四值上报**，UI 区分「服务端确认」与「客户端上报」。

### 4.2 `approval_request`（纯服务端）

`stream.py` 发 `confirm` 帧时写结构性事件（`same_turn=True`）：

```json
{ "proposalId": "…", "op": "transaction_add", "risk": "medium", "confirmReason": "amount_over_threshold", "step": 2 }
```

`op` 取**规范 op**（v1 的 `add_tx` / `add_tx_batch` 经 `canonical_op()` 归一），两条车道在后台按同一个键聚合。

不写完整 proposal 信封（体积大且与 `done.proposals` 重复）。

### 4.3 `approval_result`（三个来源）

| 来源 | 写入点 | 标记 |
|---|---|---|
| 服务端确认 | `/ai/execute` 成功，按 `proposal_id` 查 `ai_proposal_links` | `source: "server"` |
| 客户端上报 | 新增 `POST /ai/approval-result` | `source: "client"` |
| 未上报 | 无写入 | UI 显示「未上报」 |

- 客户端在确认卡收敛到终态时调用一次（四值都发）。
- 幂等键 `(user_id, proposal_id)`：重复上报覆盖最后一次，不追加行。
- 服务端校验 `conversation_id` 属于当前用户；**校验失败硬拒绝（404 `conversation_not_found`），不写入**。2026-11-10 实施口径修正：原设计的 `unverified` 语义会让伪造上报污染审计，且没有任何消费方需要它。
- **客户端上报必须走桥（2026-11-10 修正）**：WebView 侧没有任何 API base URL 与 access token——全部 HTTP 与凭据都在 Kotlin（`app/src/main/java/com/aozora/app/ai/AiGatewayClient.kt`），JS 的 `fetch` 既到不了服务端也带不上鉴权。因此需要新增桥命令 `ai.approval_result`（桥计数 **87 → 88**），由 Kotlin 带 token 调用 `POST /ai/approval-result`。**该桥命令不在 8-02 范围**（卡面禁止桥计数变动），另立 **8-02b**。
- **`/ai/execute` 请求体一字不动**（`schemas.py:115-130`）。

### 4.4 `proposal_id → conversation_id`

新增 `ai_proposal_links(user_id, proposal_id, conversation_id, turn, created_at)`，主键 `(user_id, proposal_id)`；随会话删除级联清理。写 `approval_request` 时同事务落表。

---

## 5. API 契约

前缀：`{API_PREFIX}/admin`（默认 `/api/v1/admin`）。

### 5.1 读

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/admin/users` | 用户检索 |
| GET | `/admin/users/{id}` | 用户详情 |
| GET | `/admin/users/{id}/conversations` | 该用户会话列表 |
| GET | `/admin/conversations` | 全局会话检索 |
| GET | `/admin/conversations/{id}` | 会话元信息 + 轮次摘要 |
| GET | `/admin/conversations/{id}/turns` | 轮次列表 |
| GET | `/admin/conversations/{id}/events` | 事件流；`reveal=false` 默认脱敏 |
| GET | `/admin/ai-usage/summary` | 聚合统计 |
| GET | `/admin/requests/{request_id}` | 按请求 ID 精确反查（M8-03 起事件带 `request_id` 列） |

### 5.2 写（仅限三项账户操作）

| 方法 | 路径 | 操作 |
|---|---|---|
| POST | `/admin/users/{id}/suspend` | 暂停账户（§8.1） |
| POST | `/admin/users/{id}/resume` | 解除暂停 |
| PUT | `/admin/users/{id}/ai-quota` | 设置 guest 日配额覆盖值 |
| DELETE | `/admin/users/{id}/sessions` | 强制登出全部设备 |

### 5.3 导出与审计

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/admin/export` | 三种范围 × 两种内容，流式 JSONL |
| GET | `/admin/audit` | 审计记录查询（登录 / 查看明文 / 导出 / 写操作） |
| POST | `/ai/approval-result` | 客户端审批结果上行（非 admin 前缀） |

**通用约束：** 读端点用只读事务；不复用业务 service；错误码复用 `AppException`。

---

## 6. 鉴权、部署与时间

- **多管理员账号**：`admin_accounts(id, username, password_hash, status, created_at, last_login_at)`。P0 不引入角色分级（所有管理员权限相同）。
- **独立 JWT**：单独 secret（`ADMIN_JWT_SECRET`）与 `aud=aozora-admin`。用户 token 进不了 admin API，反之亦然。
- **公网子域**：`admin.xxx`，TLS + 独立限流 + 建议 IP 白名单（部署侧可选）。
- **前端**：独立 `admin/` 包，Vite + React 19 + Tailwind + TanStack Query/Table。不进 `app/`、不进 APK。
- **时间显示**：统一北京时间（`Asia/Shanghai`）；存储仍 UTC。

---

## 7. 隐私、脱敏与审计

### 7.1 脱敏

- 服务端输出时按正则脱敏：手机号 `138****1234`、邮箱、身份证、银行卡号。
- `/events` 默认返回脱敏版；`reveal=true` 返回原文，并**写一条 reveal 审计**。
- **如实说明：** 正则只能覆盖结构化 PII；用户原话中的姓名、金额、地址无法自动识别，需靠访问审计约束。

### 7.2 审计边界（用户确认）

| 行为 | 是否记录 |
|---|---|
| 登录 | ✅ 记 `admin_audit_log(action=login)`：管理员、IP、时间 |
| 普通浏览会话 / 轮次 / 列表 | ❌ 不记 |
| 查看明文（`reveal=true`） | ✅ 记 `action=reveal` |
| 导出 | ✅ 记 `action=export` |
| 写操作 | ✅ 记 `action=write`，含 before/after 摘要 |

表结构：`admin_audit_log(id, admin_id, action, target_user_id?, conversation_id?, detail jsonb, ip, created_at)`。

### 7.3 导出（用户确认）

| 维度 | 支持 |
|---|---|
| 范围 | 当前筛选结果 / 全库 / 单用户全部会话 |
| 内容 | 明文 / 打码版 |
| 审计 | 每次导出一条 |
| 二次确认 | 不需要 |
| 实现约束 | 流式 JSONL；单次上限 100,000 事件，超出提示分批；文件名含 admin_id 与时间戳 |

全库 + 明文是最高风险组合，审计记录必须含范围、内容模式、条数与耗时。

### 7.4 隐私政策

用户确认需要新增条款：「内部人员可在限定条件下查看会话正文」。落地注意：

- `POLICY_VERSION` **不得改动**（绑 `UserConsent`）。
- 客户端 `LegalPage.tsx` 的内联副本已漂移（R-PROD-63），本次一并同步。
- 由 M8-10 承载，属于跨端文档 + UI 改动。

---

## 8. 写操作设计

### 8.1 冻结/解冻（硬约束）

**不得复用 `UserStatus.FROZEN`。** `infra/cleanup/account-deletion.py:168-175` 会物理删除 `status=FROZEN AND frozen_at <= now-7d` 的用户——后台若复用该状态，冻结 7 天后数据被删。

采用独立字段：

```
users.admin_suspended_at      timestamptz NULL
users.admin_suspended_reason  varchar(200) NULL
```

- 暂停期间：拒绝写操作与同步 push（403 `account_suspended`），数据完整保留。
- 解冻：清空两列。
- 需要覆盖的校验点：`get_current_user`、`_require_active`、sync push 入口。**这是 M8-05 的主要风险面**，必须逐点审查。

### 8.2 调整 AI 配额

`users.ai_daily_quota_override int NULL`。

- `check_daily_quota`（`server/src/modules/ai/service.py:227`）优先读覆盖值，为空则回落 `cfg.ai_daily_quota_guest`。
- **P0 仅调整 guest 日配额**；ACTIVE 用户仍不受日配额限制（与现状一致）。
- 该函数是所有 AI 调用（含 agent）的共同入口，改动必须一次到位。

### 8.3 强制登出设备

删除该用户的全部 `device_sessions`，refresh token 立即失效。影响：该用户所有设备需重新登录。

### 8.4 写审计

每次写操作写 `admin_audit_log(action=write)`，`detail` 含操作类型、目标用户、变更前后摘要。审计与写操作同事务。

---

## 9. 决策清单（2026-11-10 用户确认，锁定）

| # | 决策 |
|---|---|
| 1 | **M8 排在 M6 之前** |
| 2 | 多管理员账号（`admin_accounts`） |
| 3 | 公网子域 |
| 4 | 含少量写：冻结/解冻、AI 配额、强制登出设备；禁止改业务数据行 |
| 5 | 接受客户端增加一次四值上报 |
| 6 | 接受客户端自报，UI 标注「客户端上报」 |
| 7 | UI 区分「服务端确认」与「客户端上报」 |
| 8 | 检索只搜用户输入 + 助手回复 |
| 9 | 需要全局关键词搜索（不指定用户） |
| 10 | `ai_conversation_events` 加 `request_id` 列（精确反查） |
| 11 | 超出保留期即空态，不另存副本 |
| 12 | 只记录登录；普通浏览不记 |
| 13 | PII 默认打码；「查看明文」单独触发并记审计 |
| 14 | 允许导出：三种范围、明文与打码都支持、每次记审计、无需二次确认 |
| 15 | 需要更新隐私政策（新增内部人员查看会话正文条款） |
| 16 | 时间显示北京时间 |
| 17 | 自建 Vite + React + TanStack |

---

## 10. 待实测项（不阻塞设计）

| 项 | 何时定 | 判据 |
|---|---|---|
| `SEARCH_TEXT_MAX_CHARS` 取值 | M8-03 | 生产会话长度分布 |
| 全局搜索是否引入 `pg_trgm` | M8-03 之后 | 会话总量与 P95 延迟 |
| 单次导出上限 100,000 事件是否合适 | M8-09 | 实际导出耗时与文件体积 |
| `admin_suspended_at` 的校验覆盖点是否齐全 | M8-05 | 全仓 `_require_active` / sync push 审计 |
