# T-M7-03（P2）实施推进计划

**建立：** 2026-09-19
**关系：** 本文件是 **T-M7-03（P2：客户端瘦渲染与多会话管理）的执行细则与进度看板**。任务边界、交接产物与验收标准以 `docs/milestones/production-execution-plan.md` M7 章节为准；本文件**不改变任何验收标准**，只细化推进方式、逐卡职责边界，并记录中间结果。
**架构与决策：** `docs/architecture/ai-agent-architecture.md`（§6 上下文与会话、§8 流式协议、§9 交互设计）、`docs/adr/ADR-006`（编排位置）、`docs/adr/ADR-007`（provider 与推理策略）
**上游：** `docs/milestones/T-M7-02-P1-implementation-plan.md`（P1）——本文件 §五 承接其留给 P2 的挂账项
**用法：** 领取任一张子卡的 Agent，先读本文件对应小节，再读 `production-execution-plan.md` 该卡的验收列。

---

## 一、状态看板

| 卡 | 内容 | 状态 | 完成日 | 提交 | 备注 |
|---|---|---|---|---|---|
| **03a** | 会话索引服务端化与事件读取 API | ⏳ 未开始 | | | 批次 1；服务端面 |
| **03b** | 客户端流事件消费重构与投影缓存 | ⏳ 未开始 | | | 批次 1；客户端基座 |
| **03c** | 步骤卡片流与统一智能模式 | ⏳ 未开始 | | | 批次 2 |
| **03d** | 多会话列表与切换 | ⏳ 未开始 | | | 批次 3 |
| **03e** | 确认卡四值与取消/打断收敛 | ⏳ 未开始 | | | 批次 3 |
| **收口** | P2 四板 + 真机观感走查 | ⏳ 未开始 | | | 批次 4 |

**状态取值：** ⏳ 未开始 / 🔄 进行中 / ✅ 完成 / ⛔ 阻塞（阻塞须在本表备注列写明原因与解除条件）

---

## 二、批次与依赖

```
批次 1  03a（服务端）─┐
        03b（客户端）─┴─→ 批次 2  03c ─→ 批次 3  03d ─┐
                                     └─→ 批次 3  03e ─┴─→ 批次 4 收口
```

| 批次 | 卡 | 可并行 | 说明 |
|---|---|---|---|
| 1 | 03a、03b | ✅ 服务端 / 客户端两面，零文件重叠 | 会话权威面与客户端投影基座 |
| 2 | 03c | — | 步骤卡渲染，依赖 03b 的投影态 |
| 3 | 03d、03e | ✅ UI 区域不同 | 会话列表（侧栏/顶栏）与确认卡/取消（卡片区 + 输入区）互不重叠 |
| 4 | 收口 | — | 四板 + 真机观感走查 |

**并行切分点：** 批次 1 的 03a / 03b 与批次 3 的 03d / 03e 是两组安全切分。03c 独占批次 2 是**有意串行**——它先落地消息列表区的渲染结构，03d / 03e 再各自挂载，避免三卡同时改 `AssistantPage.tsx` 的同一段。

**为什么 03a 在「客户端」任务里：** P2 的标题是客户端瘦渲染，但**多会话的权威面在服务端**——架构 §6.1 定「存储 = 客户端本地 + 服务端 `AIConversation` 摘要」、§6.3 定「服务端持有会话状态，客户端只传 `conversation_id`」。且 P1 的 02f 决策 7 明确把「会话归属维度」的复核责任交给了 P2（见 §五）。故 03a 是 P2 的组成部分，不是越界。

**外部阻塞（开工前置）：** T-M7-02 的客户端侧（02h 桥通道、02i 最小消费）未收口前，03b 拿不到冻结的流事件契约。**解除条件** = 02h / 02i 交付且八类事件载荷不再变动。

---

## 三、逐卡细则

> 每卡均含「内容 / 职责边界 / 产出 / 验证」。**职责边界是防范围蔓延的关键，不得越界**。

### 03a 会话索引服务端化与事件读取 API

- **内容：** 把 `AIConversation`（现仅 `id` / `user_id` / `book_id` / `summary` + `AuditMixin`，`server/src/models/ai.py:50`）扩展为**可列表的会话索引**：`title`（默认取首轮用户输入截断）、活跃时间（复用或新增）、软删字段（与既有实体 `archived_at` 口径一致）。`ConversationService`（`modules/ai/conversation.py`，现 6 个方法）扩 `list_conversations` / `rename` / `delete` / `events(after_seq=None)`。`routes.py`（现 6 个 AI 端点）增四个：`GET /ai/conversations`、`PATCH /ai/conversations/{id}`、`DELETE /ai/conversations/{id}`、`GET /ai/conversations/{id}/events`。新增迁移（紧接 `0018_conversation_event_turns`）。**复核并落地 P1 挂账「登出按用户粒度清会话」**（§五 第 1 项）。
- **职责边界：** **不做客户端**（03b / 03d）。**不改 `/ai/agent` 的事件协议**——八类事件一个不多一个不少。**不做事件日志的完整形态**（`ignorable` / Surface 子集归 P3 的 04a），本卡的 `events()` 只按既有 `type` 原样读出。**不实现摘要**（`summary` 保持空串，归 04c）。
- **产出：** `server/src/models/ai.py`、新迁移 `0019_*`、`server/src/modules/ai/conversation.py`、`routes.py`、`schemas.py`、`server/tests/test_ai_conversations_api.py`
- **验证：** pytest——列表只返回本人会话且按活跃时间排序；重命名持久化、跨用户一律 404；删除幂等且事件行级联清理（沿用 `ON DELETE CASCADE` 口径）；`events(after_seq)` 支持增量读取；登出 / 删号清理行为与 §五 第 1 项的结论一致；既有 `test_ai_conversation.py`（676 行）不回归。

### 03b 客户端流事件消费重构与投影缓存

- **内容：** 把 `use-assistant.ts`（**1272 行**，含本地 NLU / AI 单发 / agent 流式三路）的 **agent 流式消费段**（现 `:157–254` 类型 + `:670–799` `settleStream` / `runAgentTurn`）抽成独立模块，事件 → 状态的 reducer 与 hook 分离；`useAssistant` 只保留编排与卡流程。`assistant-chat-store`（`app/src/platform/store/assistant-chat-store.ts`，**70 行**、扁平 `messages[]`）改为**按 `conversationId` 分桶**并降级为 UI 投影缓存（持久化形状升级 + `version` bump + **旧 `aozora-assistant-chat` v1 数据迁移**）。`conversationId` 由 hook 内存 ref（`use-assistant.ts:357`，切页 remount 即丢）提升到 store。**`ai.cancel` 接线**——TS 侧已有 `cancelAgent`，但 hook 未调用（取消收敛是 03e 的前置）。
- **职责边界：** **不做步骤卡 UI**（03c）、**不做会话列表 UI**（03d）、**不动 Kotlin 桥**（02h 已交付）。**不引入新桥命令**。三个互斥瞬时态（`activeProposal` / `pendingOp` / `pendingConfig`）的约束不变——**重构只搬家不换语义**。
- **产出：** `app/src/features/assistant/use-assistant.ts`（拆分）、新 `app/src/features/assistant/use-assistant-stream.ts`、`app/src/platform/store/assistant-chat-store.ts`、必要的类型补充
- **验证：** vitest——八类事件（`phase` / `tool_call` / `tool_result` / `confirm` / `device_request` / `delta` / `done` / `error`）→ 投影态逐条断言；`delta` 按 `channel` 分流（`reasoning` 进折叠区、**绝不混进正文**）；`error` 帧到达时**保留半截文本并标注失败、不清空**；**不可假设「每条回复都从 `delta` 开始」**（快档直调工具那一步 0 帧，见 §五）；会话切换互不串扰；取消后三态收敛为空且零写；v1 持久化数据可迁移；既有 13 个 assistant 测试文件全绿（搬家导致的 import 调整计入本卡）；`npx tsc --noEmit` 0 错。

### 03c 步骤卡片流与统一智能模式

- **内容：** 把 `phase` / `tool_call` / `tool_result` 渲染为**可折叠的步骤卡片流**（进行中 / 完成 / 失败三态；`tool_result` 只显示摘要，不含原始数据）。**统一智能模式**：无模式开关，简单任务（本回合无 `tool_call`）不展示步骤区，长任务才展开——复杂度对用户不可见（架构 §9.1）。推理增量（`delta.channel === 'reasoning'`）的灰字折叠区**打磨**（02i 已落地最小形态，见 `AssistantPage.tsx:601–634`）。`routing` 阶段的空态占位（生产者归 P4 的 05g）。
- **职责边界：** **不做会话列表**（03d）、**不做确认卡与取消**（03e）。**不改服务端**。**不引入新事件类型**（八类不动）。
- **产出：** 新 `app/src/features/assistant/StepCardStream.tsx`（或等价命名）+ `AssistantPage.tsx` 挂点 + 样式对齐 `docs/design/tokens.md`
- **验证：** vitest——事件序列渲染三态齐全（含 `tool_result` 失败态）；简单任务无步骤区；折叠 / 展开；多帧 `delta` 追加渲染（02g-2 硬约束③）；空 / 加载 / 失败 / 离线四态回归（`docs/APP-CONTEXT` 10.x 的 UI 门禁）。

### 03d 多会话列表与切换

- **内容：** 会话列表入口（侧栏 / 抽屉）与**新建 / 切换 / 重命名 / 删除**，对接 03a 的四个端点。`book_id` 归属与助手当前账本的联动（切换会话时账本是否跟随，须给出明确规则）。离线 / 未登录时的降级（只读本地投影缓存、禁用新建）。删除当前会话后的回退目标须确定。
- **职责边界：** **不做步骤卡**（03c）、**不做确认卡**（03e）。**不做会话正文跨设备同步**（归 P3 的 04e）。**不改事件协议**。
- **产出：** 新 `app/src/features/assistant/ConversationList.tsx`（或等价）+ `AssistantPage.tsx` 挂点 + store 接线
- **验证：** vitest——新建 / 切换 / 重命名 / 删除各有用例；两会话消息互不串扰；删除当前会话后回退到确定性目标；离线降级不报错；快路径 12 s / 25 s 体验不变。

### 03e 确认卡四值与取消/打断收敛

- **内容：** 确认卡对接**审批四值** `allowed-once` / `rejected` / `cancelled` / `unavailable`（`unavailable` **视为拒绝**，02e 口径）。把服务端 `confirm` 事件与既有 `activeProposal` 卡链路打通——P1 是「终态按 v2 intent 分发渲染」，P2 起改为**事件驱动**。**取消按钮（`ai.cancel`）与打断交互收敛到一致状态**（架构 §4.4 不变式 3），并给出取消的服务端可观测形态（P1 的 `StopReason.CANCELLED` 不可达，见 §五）。**中断状态提示**（R-PROD-19：弱网 / 后台断连）。语音打断语义同取消（架构 §9.2）。**「取消」是用户主动、断连是被动**——两者的 UI 表现与恢复路径不同，不得合并成一个状态。
- **职责边界：** **不改服务端审批逻辑**（02e 已交付，四值语义不动）。**不改写路径**。**不做步骤卡**（03c）。
- **产出：** `app/src/features/assistant/ConfirmationCard.tsx`（824 行）、`use-assistant.ts`、`AssistantPage.tsx`
- **验证：** vitest——四值各自的 UI 表现；`unavailable` 视为拒绝且**零写**；取消后三态收敛、**无悬挂确认卡**；断连提示出现且给出重连入口；既有 `confirmation-card.test.tsx`（544 行）全绿。

### 收口（批次 4）

- P2 四板全绿（见 §四）
- 真机观感走查：步骤卡片流（含失败态）、多会话新建 / 切换 / 删除、取消与打断
- 快路径 12 s / 25 s 回归
- P2 出口标准逐条核对（`production-execution-plan.md` T-M7-03 验收列）

---

## 四、推进机制

### 提交规则

- **每卡一次独立提交**，**逐文件精确 add**（`git add <本卡文件>`），**绝不 `git add -A`**。**并发会话下用 `git commit --only <路径>`**（提交的是整个暂存区，`--only` 只到文件级）。
- 跨卡共享文件（`use-assistant.ts`、`AssistantPage.tsx`、`assistant-chat-store.ts`）由**后完成**的卡负责解决冲突，不得顺手改其他卡的文件。

### 验证基线（每卡完成后必跑）

```
cd server && python -m pytest tests/ -q          # 每卡只允许在基线上增加，不得减少
./gradlew :app:testDebugUnitTest --rerun-tasks   # gradlew 在仓库根目录，须从仓库根运行
cd app && npx vitest run                         # 不带 --rerun-tasks 会 UP-TO-DATE，拿不到真实数字
cd app && npx tsc --noEmit                       # 0 错
```

> **基线数值以开工时实测为准，不在本文件写死。** 参考：02g-2 交付后（2026-09-19）服务端 1029 passed + 37 skipped / JVM 394 tests / vitest 1107 passed / tsc 0 错。
> ⚠️ **多会话陷阱（继承自 P1 计划 §四）：** 并发会话下跑全量套件的数字**不可作为本卡基线**——他人卡的在途测试文件会被一并计入。须以 `pytest <本卡测试文件>` 的增量与 `--collect-only` 为准。

### 阻塞处理

- 卡内发现需改**架构决策**时**停下来先改文档**再动代码（`docs/engineering/需求变更规范.md` 对 AI 协议类改动的硬要求）。
- 出现阻塞：本文件 §一 状态看板改为 ⛔ 并写明「原因 + 解除条件」，同时更新 `docs/risk-register.md`。
- 不替其他卡修问题，不删别人的临时产物。

### 卡完成时的记录义务

每卡完成后，在本文件 §六 追加一条中间结果记录，按 `production-execution-plan.md` 末尾的**交接模板**填写（任务编号 / 完成范围与未做 / 修改的文档契约代码 / 验证命令与结果 / 数据同步 AI 安全影响 / 兼容与回退方式 / 开放风险与责任人 / 下一项未阻塞任务）。

---

## 五、承接自 P1 的挂账项

以下条目由 `docs/milestones/T-M7-02-P1-implementation-plan.md` §五 明确指派给 P2，**不得在 P2 中丢失**：

| # | 挂账 | 落卡 | 处理方式 |
|---|---|---|---|
| 1 | **登出按用户粒度清会话**（02f 决策 7）——多设备场景下会误伤另一台设备的会话，且影响 `purge_user` 的粒度 | **03a** | 本卡给出结论并落地（保持 / 改粒度 / 引入设备维度）。P1 时客户端单会话、无跨设备正文同步，影响面有限；P2 引入会话归属维度后必须重审 |
| 2 | **快档 + 直接调工具时这一步没有任何 `delta`**（02g-2 探针 P1：0 帧） | **03b / 03c** | 渲染**不能假设「每条回复都从 `delta` 开始」**；「有反应」在该形态下靠 `tool_call` / `tool_result` |
| 3 | **`StopReason.CANCELLED` 在 P1 不可达**——断连走的是连接取消（不发帧），审批通道也未接 | **03e** | P2 把取消做成主动交互后，须重新定义「取消」的服务端可观测形态，并让客户端收敛到一致状态 |
| 4 | **客户端层灰度开关未落地**（02g 决策 6）——责任人原为 02i | — | **已由 02i 落地**（`AI_AGENT_CLIENT_ENABLED`，`use-assistant.ts:171`，默认 false）；P2 各卡不得改动其默认值 |
| 5 | **合并窗两个参数是初值**（100 ms 与「同通道 + 同 Step」分组键）——真实分布要等 02h / 02i 接通后统计 | **03c / 收口** | 步骤卡与流式渲染落地后按真实帧分布复核；责任人原为「收口」 |
| 6 | **R-PROD-19（SSE 弱网 / 后台中断）**——客户端明确提示中断状态 | **03e**（提示）+ **P3 的 04b**（恢复） | P2 只做提示与重连入口；按 `conversation_id` 续跑归 P3 |

---

## 六、中间结果记录

_（尚无。每卡完成后按交接模板在此追加。）_

---

## 七、参考

- `docs/milestones/production-execution-plan.md` M7 章节 — 任务边界与验收（**权威**）
- `docs/architecture/ai-agent-architecture.md` — 目标架构（§6 上下文与会话、§8 流式协议、§9 交互设计）
- `docs/milestones/T-M7-02-P1-implementation-plan.md` — P1 逐卡细则与中间结果（本文件 §五 的来源）
- `docs/architecture/ai-architecture.md` — 当前已实现契约
- `docs/design/tokens.md` — 青空设计令牌（03c / 03d / 03e 的 UI 实现须遵守）
- `docs/risk-register.md` R-PROD-19 / 20 / 22 — P2 相关风险
- `docs/engineering/需求变更规范.md` — L1/L2/L3 变更级别与文档先行要求
- `docs/engineering/通用任务执行规范.md` — 开发实现 / Review / Bug-fix 三类工作流