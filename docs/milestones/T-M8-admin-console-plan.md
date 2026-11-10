# T-M8 运营可观测后台 · 实施推进计划

**建立：** 2026-11-10
**关系：** 本文件是 **M8（运营可观测后台：AI 交互回放台）的执行细则与进度看板**。架构事实源见 [`docs/architecture/admin-console.md`](../architecture/admin-console.md)；本文件不重复架构细节，只定义卡片边界、依赖与验收。
**需求来源：** 用户 2026-11-10 下达（第二十二轮 REQ-84）。两条硬需求：① **审批过程必须进入事件流**；② **支持按用户 ID + 会话关键词 + 时间检索定位会话**。
**用法：** 领取任一子卡的 Agent 先读架构文档对应章节，再读本文件该卡小节。**职责边界是防范围蔓延的关键，不得越界。**

---

## 一、状态看板

| 卡 | 内容 | 状态 | 完成日 | 提交 | 备注 |
|---|---|---|---|---|---|
| **8-01** | 文档与口径裁决（文档先行） | ⏳ 未开始 | | | 本文件 + 架构文档 + APP-CONTEXT §4.6 + ai-agent-architecture §6.2 |
| **8-02** | 审批留档：`approval_request` / `approval_result` | ⏳ 未开始 | | | 含 `ai_proposal_links` 与客户端上行端点 |
| **8-03** | 会话检索数据面：`search_text` + 回填 + 清理联动 | ⏳ 未开始 | | | 迁移与清理重算 |
| **8-04** | admin 鉴权与只读 API | ⏳ 未开始 | | | `admin_accounts` + 独立 JWT + 查询层 |
| **8-05** | admin 前端壳 + 用户/会话检索页 | ⏳ 未开始 | | | 独立 `admin/` 包 |
| **8-06** | **会话回放页**（核心） | ⏳ 未开始 | | | 轮次 + 事件时间轴 + 配对渲染 |
| **8-07** | 聚合统计与 `request_id` 反查 | ⏳ 未开始 | | | token / 成本 / 成功率 |
| **8-08** | 部署、访问审计与 PII 策略 | ⏳ 未开始 | | | nginx + `admin_access_log` |
| **8-09** | 收口 | ⏳ 未开始 | | | 四板 + 真实数据走查 |

**状态取值：** ⏳ 未开始 / 🔄 进行中 / ✅ 完成 / ⛔ 阻塞（备注列写明原因与解除条件）

---

## 二、批次与依赖

```
批次 1  8-01 ─→ 批次 2  8-03 ─┬→ 批次 3  8-02 ─┐
                                │                ├→ 批次 4  8-04 ─┬→ 批次 5  8-05 ─→ 8-06 ─┐
                                └────────────────┘                └→ 批次 5  8-07 ──────────┴→ 批次 6  8-08 ─→ 批次 7  8-09
```

| 批次 | 卡 | 可并行 | 说明 |
|---|---|---|---|
| 1 | 8-01 | — | 文档先行：口径裁决未定，后面所有卡都可能返工 |
| 2 | 8-03 | — | **迁移链硬串行**：8-03 的迁移先占 revision，8-02 的新表迁移排在其后 |
| 3 | 8-02 | — | 代码可与 8-03 并行写，但**迁移必须后落**（revision 不得分叉） |
| 4 | 8-04 | — | 检索与审批数据面都要就位，API 才有完整形状 |
| 5 | 8-05、8-07 | ✅ 不同页面/端点 | 8-06 依赖 8-05 的壳与路由 |
| 6 | 8-08 | — | 部署前须有可运行的只读 API |
| 7 | 8-09 | — | 四板 + 真实数据走查 |

**为什么 8-01 必须先行：** 本需求与 `APP-CONTEXT §4.6`「禁止记录完整对话」直接冲突（而事件表早已保存正文）。不先裁决口径，8-02/8-04 的隐私设计无从谈起，且审阅时会反复拉扯。

---

## 三、逐卡细则

### 8-01 文档与口径裁决

- **内容：**
  1. 本文件与 `docs/architecture/admin-console.md` 定稿。
  2. `docs/APP-CONTEXT.md` §4.6 措辞裁决：应用日志/日志系统仍禁止完整对话；会话正文仅存事件日志，受保留期与访问审计约束。
  3. `docs/architecture/ai-agent-architecture.md` §6.2 事件类型表：`approval_request` / `approval_result` 的生产者从「待接」改为「M8-02」。
  4. `docs/APP-CONTEXT.md` 第 9 节新增 M8 任务树；`docs/milestones/overview.md` 增加 M8 行。
  5. 确认开放项：管理员是否多账号、写能力是否纳入 M8（架构文档 §8）。
- **职责边界：** **零代码改动**。不改任何 `.py` / `.ts` / 迁移。
- **产出：** 上述文档。
- **验证：** 文档间引用一致（M8 编号、REQ-84、卡片依赖）；`APP-CONTEXT` 第 9 节与 overview 的里程碑表无矛盾。

### 8-02 审批留档

- **内容：**
  1. `approval_request`：`stream.py` 发 `confirm` 帧时写结构性事件（`same_turn=True`），载荷 `proposalId / op / risk / confirmReason / step`，**不写完整 proposal 信封**。
  2. 同事务写 `ai_proposal_links(user_id, proposal_id, conversation_id, turn, created_at)`，主键 `(user_id, proposal_id)`。
  3. `approval_result`：`/ai/execute` 成功时按 `proposal_id` 查 `ai_proposal_links` 写 `allowed-once`；其余三值由新增 `POST /ai/approval-result` 接收（客户端在卡收敛时上报）。
  4. 客户端（TS）：确认卡四值收敛处调用一次上行；未升级客户端不得阻塞。
- **职责边界：** **不改 `/ai/execute` 请求体**（`AIExecuteProposalRequest` 一字不动）。**不改审批策略**（02e 的四值与 zero-write 语义不动）。**不新增桥命令**（WebView 直接 `fetch`，桥计数 87 不变）。**不把 `device_request` / `security` 顺手接上**。
- **产出：** `server/src/modules/ai/stream.py`、`conversation.py`、`routes.py`、`schemas.py`、`server/src/models/ai.py` + 迁移、`app/src/**`（卡收敛处）、测试。
- **验证：**
  - pytest：`confirm` 帧产生一条 `approval_request` 且与消息同 `turn`；`/ai/execute` 成功产生 `allowed-once`；四值上行幂等（同 proposal 重复上报不追加行）；`conversation_id` 越权被拒。
  - 回归：既有审批用例（zero-write、四值语义）全绿；`/ai/execute` 老请求体行为逐字不变。
  - **迁移 revision 必须接在 8-03 之后，不得分叉。**

### 8-03 会话检索数据面

- **内容：**
  1. 迁移：`ai_conversations.search_text text NOT NULL DEFAULT ''`。
  2. `ConversationService.append_events` 在写 Surface 事件时同事务追加 `user` / `assistant` 的 `content`；`tool` 不进检索面。
  3. 上限 `SEARCH_TEXT_MAX_CHARS`（初始 4096，保留尾部）与截断标记。
  4. `purge_expired_events` 删除事件后**重算** `search_text`（与正文同寿，不得留残影）。
  5. 回填脚本：从历史事件聚合。
- **职责边界：** **不改事件表结构**、**不引入 `pg_trgm`**（P0 由 `user_id` 索引先过滤，架构 §3.4）。**不改客户端同步协议**（`search_text` 是服务端内部列，不进 pull/push）。
- **产出：** 迁移、`conversation.py`、回填脚本、测试。
- **验证：**
  - pytest：写入后 `search_text` 含用户与助手原话、不含 tool 结果；超限截断；保留期清理后 `search_text` 不再含被删正文（**反向验证：删前断言含、删后断言不含**）。
  - 真实 PG 取证：迁移在 PG 18 上通过；回填脚本对存量数据幂等。
  - SQLite 测试后端不需要新语法（普通 `text` 列 + `LIKE`）。

### 8-04 admin 鉴权与只读 API

- **内容：**
  1. `admin_accounts` 表 + `ADMIN_JWT_SECRET` / `aud=aozora-admin` + 登录/刷新/登出。
  2. `modules/admin/`：`routes.py` / `dependencies.py` / `schemas.py` / `queries.py`。
  3. 端点按架构 §5 实现；全部只读事务；不复用业务 service。
  4. `GET /admin/requests/{request_id}`：`ai_usage.request_id` → conversation + 时间窗内 turn。
- **职责边界：** **不写任何业务表**。**不暴露事件正文于列表端点**。**不复用 C 端限流器**。**不改现有 `modules/*` 的任何路由**。
- **产出：** 上述文件 + `main.py` 注册 + 测试。
- **验证：** 用户 JWT 访问 admin API 返回 401；管理员 JWT 访问 C 端 API 返回 401；分页 keyset 无重复/遗漏；列表响应不含 `data` 正文；只读事务下误写语句报错。

### 8-05 admin 前端壳 + 用户/会话检索页

- **内容：**
  1. 独立 `admin/` 包（Vite + React 19 + Tailwind + TanStack Query/Table）。
  2. 登录页、左侧分组菜单、通用筛选栏 + 宽表格 + 分页 + 详情抽屉（参照用户 2026-11-10 提供的 X5 后台形态）。
  3. 用户检索页、会话检索页、用户详情页（会话列表）。
- **职责边界：** **不进 `app/`**、不共享移动端设计令牌、不改 Android 构建。**不做回放页**（8-06）。**不做写操作**。
- **产出：** `admin/` 全量。
- **验证：** 手机号 → 用户 → 会话列表可走通；筛选组合（用户 + 关键词 + 时间）结果正确；空态/加载/失败态齐备；桌面与窄屏均无横向溢出。

### 8-06 会话回放页（核心）

- **内容：**
  1. 左侧轮次列表（turn / 首条用户输入 / model / tokens / 是否报错 / 耗时），右侧事件时间轴。
  2. `tool_calls` 与配对 `tool` 结果自动成组渲染；`reasoning_content` 默认折叠；原始 JSON 一键展开。
  3. 结构性事件（`quota` / `error` / `replace` / `approval_request` / `approval_result`）按类型着色与语义化渲染。
  4. 「该轮已超出保留期」与「审批结果未上报」两种空态。
- **职责边界：** **只读**；**不提供导出**（P0）。**不做跨会话对比**。
- **产出：** `admin/src/pages/ConversationReplayPage.tsx` 等。
- **验证：** 用一条真实会话逐轮走查：用户输入、工具调用、工具结果、模型回复、token 一致；错误轮显示错误码；审批轮显示提问理由与四值（升级客户端）；与 PG 直查 `ai_conversation_events` 的 `seq`/`turn` 顺序完全一致。

### 8-07 聚合统计与 `request_id` 反查

- **内容：** 按 user / model / day 的 token 与成本、会话数、成功率、P95 轮耗时；`request_id` 反查入口。
- **职责边界：** 统计只读 `ai_usage` / 事件表；**不引入 Prometheus**。
- **验证：** 数字与 PG 直查一致（同一 SQL 口径）；反查在并发会话下标注「疑似相邻轮次」而不是硬断言。

### 8-08 部署、访问审计与 PII

- **内容：** `admin_access_log` 表与写入点；nginx 配置；手机号打码；部署文档；管理员限流。
- **职责边界：** 不开放公网匿名访问；不提供批量导出。
- **验证：** 未登录 401；越权 token 401；每次 `/events` 访问落审计行；ng 配置经 `nginx -t`。

### 8-09 收口

- **内容：** 四板全量（tsc / vitest / JVM / pytest）+ 真实会话走查 + 文档回填 + 风险册登记（架构 §7 四条）。
- **验证：** 四板全绿；真实数据走查覆盖「用户检索 → 会话定位 → 回放 → 错误归因」全链；`docs/risk-register.md` 新增条目编号连续。

---

## 四、领卡通用红线

1. **不碰 `app/` 的 Android 构建**（8-02 的客户端上行除外，它只改 TS 调用点，不加桥命令）。
2. **迁移 revision 不得分叉**；8-02 的迁移排在 8-03 之后。
3. **后台 API 一律只读事务**；写能力不在 M8。
4. **列表不返回事件正文**；正文只在 `/events`。
5. **审批四值不得合并**；老客户端未上报时显示「未上报」，不得推断为拒绝。
6. **`search_text` 与事件同寿**：清理事件必须同步裁剪检索文本。
