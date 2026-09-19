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
| **03a** | 会话索引服务端化与事件读取 API | ✅ 完成 | 2026-09-19 | `985ee2d` | 批次 1；服务端面 |
| **03b** | 客户端流事件消费重构与投影缓存 | ✅ 完成 | 2026-09-19 | `1cc0fba` | 批次 1；客户端基座 |
| **03c** | 步骤卡片流与统一智能模式 | ✅ 完成 | 2026-09-19 | `98ac17f` | 批次 2 |
| **03d** | 多会话列表与切换 | ✅ 完成 | 2026-09-19 | `a39293d` | 批次 3；**扩桥 3 命令**（79→82） |
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
| 1 | **登出按用户粒度清会话**（02f 决策 7）——多设备场景下会误伤另一台设备的会话，且影响 `purge_user` 的粒度 | **03a** | 本卡给出结论并落地（保持 / 改粒度 / 引入设备维度）。P1 时客户端单会话、无跨设备正文同步，影响面有限；P2 引入会话归属维度后必须重审。**已落地（985ee2d，2026-09-19）：保持按用户粒度清、不引入设备维度**——`ai_conversations` 无设备列、`/ai/agent` 也不带 `device_id`，加设备维度要同时改请求契约与 02h 已冻结的桥，而跨设备正文同步归 04e 的决策门。代价（多设备下另一台机器的服务端会话消失）已知并接受；**客户端的 404 降级契约（丢弃该 id、开新会话）落 03b / 03d**，详见 §六 03a 记录 |
| 2 | **快档 + 直接调工具时这一步没有任何 `delta`**（02g-2 探针 P1：0 帧） | **03b / 03c** | 渲染**不能假设「每条回复都从 `delta` 开始」**；「有反应」在该形态下靠 `tool_call` / `tool_result`。**已落地（03c，`98ac17f`）**：步骤区本就不依赖正文——0 帧 `delta` 的一轮里步骤区照常在场、正文走「思考中…」占位，有用例钉住 |
| 3 | **`StopReason.CANCELLED` 在 P1 不可达**——断连走的是连接取消（不发帧），审批通道也未接 | **03e** | P2 把取消做成主动交互后，须重新定义「取消」的服务端可观测形态，并让客户端收敛到一致状态 |
| 4 | **客户端层灰度开关未落地**（02g 决策 6）——责任人原为 02i | — | **已由 02i 落地**（`AI_AGENT_CLIENT_ENABLED`，`use-assistant.ts:171`，默认 false）；P2 各卡不得改动其默认值 |
| 5 | **合并窗两个参数是初值**（100 ms 与「同通道 + 同 Step」分组键）——真实分布要等 02h / 02i 接通后统计 | **03c / 收口** | 步骤卡与流式渲染落地后按真实帧分布复核；责任人原为「收口」。**渲染面已落地（03c）**；**复核动作仍未做**——它要的是**真机真实帧分布统计**（单测层拿不到），随批次 4 收口的真机走查一并做。**责任人：M7 收口** |
| 6 | **R-PROD-19（SSE 弱网 / 后台中断）**——客户端明确提示中断状态 | **03e**（提示）+ **P3 的 04b**（恢复） | P2 只做提示与重连入口；按 `conversation_id` 续跑归 P3 |

---

## 六、中间结果记录

### 03d · 多会话列表与切换（✅ 2026-09-19）

**任务编号：** T-M7-03d　**提交：** `a39293d`

**完成范围 / 明确未做：**

- **完成：**
  - **前置发现：桥里没有一条通道能到 03a 的四个端点**（无 `conversation.*` 命令、
    无通用 HTTP 命令、`executeAuthorized` 只支持 POST）。所以本卡先扩桥，再在其上做 UI。
  - **桥（79→82）**——`conversation.list` / `.rename` / `.delete` 三命令；
    `AiError.NotFound` 与两处 404 映射（同步路径 + SSE 路径）；`executeAuthorized`
    支持 GET/PATCH/DELETE（既有 POST 调用点走默认参数，行为不变）；204 空响应体由
    handler 造 `{success:true}`。
  - **客户端**——新 `use-conversations.ts`（拉取 / 降级 / 确定性回退）与
    `ConversationList.tsx`（抽屉，行内重命名 + 行内删除确认）；`AssistantPage` 顶栏
    入口与挂点；`assistant-chat-store` 增 `forgetConversation` / `removeConversation`。
  - **两条规则的定案**（卡面要求「须给出明确规则」的两处）：
    - **`book_id` 只记录不驱动**——切换会话**不改变**当前记账账本（每轮仍用页面当前
      `bookId`），避免「切个会话把全局默认账本改了」的副作用；
    - **删除后的回退目标 = 列表里剩下的第一条**（服务端按活跃倒序 → 最近活跃的另一条），
      一条不剩则回退到本地会话桶。目标由 `use-conversations` 算好传给 store，
      store 只执行。
  - **清理 03b 交办的挂账**（陈旧 `conversation_id` 的 404 降级）：服务端在**开流之前**
    回 404，桥把它译成 `error{code:'not_found'}` 帧 → `forgetConversation()` 丢 id、
    **当前这一屏消息原地保留** → 下一轮不带 id，服务端开新会话。
- **明确未做（逐条对应卡面职责边界）：**
  1. **不做步骤卡**（03c，同批已交付）；
  2. **不做确认卡与取消**（03e）；
  3. **不做会话正文跨设备同步**（归 P3 的 04e）——`GET /ai/conversations/{id}/events`
     因此**有意不加桥命令**（加一条无人调用的命令只扩大 APK 契约面，P3 接正文恢复时再补）。
     代价：切到一条本机没缓存过的会话时消息区是空的，UI 明确写出「这条会话的消息不在
     这台设备上」，不假装是新会话；
  4. **不改事件协议**——八类事件一个不多一个不少（本卡只多了一个**错误码**
     `not_found`，它属桥侧本地失败那一族）；
  5. **不改服务端**——一行 Python 未动。

**关键设计决策：**

| # | 决策 | 理由 |
|---|---|---|
| 1 | **404 单列 `AiError.NotFound`**，不并进 `Network` | 服务端对它有一层确定语义（别人的 / 已删的 / 不存在的 id 同码）。并进 Network 会让「丢弃陈旧 id」与「提示重试」这两条完全不同的处置**无从分辨**——这是 03b 记录的原始缺口 |
| 2 | `forgetConversation` **不动消息**（≠ `switchConversation(null)`） | 切换会把本地桶的旧内容顶上来，用户会看到「内容凭空变了」；丢 id 的语义是「不再挂在那个 id 上」，不是「换一条会话」 |
| 3 | **流式期间禁止切换 / 新建 / 重命名 / 删除** | 这是 03b 收口记录留给 03d 的设计选择题。客户端 `messages` 是扁平的、`setMessages` 的十余处调用点不带会话目标，流中切换会让这一轮的消息落到「切过去之后」的会话里；根治要把一轮绑到发起会话（跨多卡改动），本卡用禁用守住不变量 |
| 4 | **降级是渲染期派生，不落 state** | 桥能力与连通性都不是异步事实；塞进 state 会在 effect 里产生一次同步 setState（级联渲染，本仓 `react(set-state-in-effect)` 门禁）。落态一律留在 `.then` / `.catch` 回调里 |
| 5 | 降级态**允许切换**、只禁新建与写操作 | 卡面「只读本地投影缓存、禁用新建」——切换只读缓存，不是写 |
| 6 | 桥的错误映射复用**一个**私有 `conversationCall` | 三个 handler 的 catch 分支完全一致；这是新增代码的内部复用，**既有 `handleAi*` 的逐 catch 形态一行未动** |

**验证命令与结果：**

| 命令 | 结果 |
|---|---|
| `cd app && npx vitest run` | **82 files / 1206 passed**（03c 交付后基线 81 / 1188 → 本卡 +1 文件 / +18 用例） |
| `cd app && npx tsc --noEmit` | **0 错** |
| `cd app && npm run lint` | exit 0；本卡新增的 5 个文件**零告警**（既有告警集合不变） |
| `bash ./gradlew :app:testDebugUnitTest --rerun-tasks` | **418 tests / 0 failures / 1 skipped**（基线 406 → +12，即两个新 Kotlin 测试文件） |
| `bash ./gradlew :app:lintDebug :app:assembleDebug :app:assembleDebugAndroidTest` | **BUILD SUCCESSFUL**；其中 `compileDebugAndroidTestKotlin` **确实执行**（非 UP-TO-DATE）——本卡动了主源码的 `AiError` 与 `executeAuthorized` 签名，**androidTest 源集能编译**是本仓踩过的坑（本地 JVM 单测绿 ≠ androidTest 能编译） |
| `cd app && npm run typecheck` | exit 0（`tsc --noEmit` + `tsc -p tsconfig.node.json`，即 CI Client 作业的口径） |
| `cd app && npm audit --audit-level=high` | 0 vulnerabilities |
| `cd server && python -m pytest tests/ -q` | **1075 passed / 37 skipped**（与 03a 基线逐字一致；本卡未触碰任何 Python 文件） |

> **远端 CI：本轮按要求未确认**（推送已完成，未等待 / 未查看远端结果）——按「不得写成通过」的口径，此处记「未确认」而非「通过」。本卡所有结论以上表的**本地 CI 等价命令**为准。
> `dependencyCheckAnalyze` 未跑：本卡**未新增任何依赖**（`package.json` / `build.gradle.kts` 一行未动），该任务是长耗时且依赖 NVD 库下载的网络任务。

**回退实验（5 处变异，逐条回退后实测）：5/5 变红** —— Kotlin 2 处：同步路径 404 分支去掉
（`conversationEndpoints_map404ToNotFound` 红）、SSE 路径 404 分支改回 `Network`
（`agentStream_maps404ToNotFound_staleConversationId` 红，另有两条桥侧用例连带红）；
客户端 3 处：删除回退目标改成恒 `null`（「回退到确定性目标」红）、`forgetConversation`
顺手清空消息（store 级 + hook 级两条红）、删除时把 404 当失败（「404 与成功同义」红）。
每处都按 sha256 逐字节还原。

**数据 / 同步 / AI / 安全影响：**

- **同步：零影响**——不进 sync 协议，无 `sync_mutations` 写入。
- **AI：** 新增三条管理命令；`/ai/agent` 行为不变，只是失败面多了一个可分辨的
  `not_found` 码。**新增代码路径为零写**（列表 / 重命名 / 删除都不碰账本）。
- **安全：** 会话列表是继 03a 之后的**第二个读取面**（列表与删除都经服务端归属校验）；
  客户端对 404 的处置是「丢弃 + 静默」，**不把服务端文案透传**（错误文案一律走本地映射表，
  未知码走兜底）。
- **隐私：** 用户主动删除会立即清掉服务端事件正文（03a 已交付）；客户端同时清掉该会话的
  本地投影缓存。

**兼容与回退方式：**

- **APK 与 JS 版本不同步是预期形态**：桥缺这三条命令时能力探测为假 → 会话列表整块降级为
  「本机缓存 + 禁用新建」，不炸页面（与 02h / 03b 的探测口径一致）。
- 回退 = revert 本提交（桥命令是纯增量，旧 JS 不会调用它们）。
- `AI_AGENT_CLIENT_ENABLED` 默认值未动（§五 第 4 项）。

**开放风险与责任人：**

- **端到端真机路径仍未跑过**（02h / 03b / 03d 的共同缺口）——本卡全部验证停在单测层，
  且**新增了三条 APK 契约**（须重新构建 APK 才生效）。收口须在模拟器 / 真机上走查：
  打开列表 → 新建 → 切换 → 重命名 → 删除 → 删除当前会话后的回退。
  **责任人：M7 收口。**
- **流式期间禁止切换是权宜**——根治（一轮绑定发起会话）未做，已记入架构文档与风险登记册。
  **责任人：后续卡 / M7 收口评估。**
- **切到无本地缓存的会话时消息区为空**（正文跨设备同步归 04e）。**责任人：P3 的 04e。**
- **「新建」在「已经处于本地会话桶且桶内有内容」时是空操作**——`switchConversation(null)`在 `from === to` 时直接返回。这是**非破坏性**的选择（宁可让用户看到那屏旧内容，也不静默清空）：本地桶只在失败轮 / 离线轮之后才有内容。若收口走查认为该场景需要一个显式的「清空并新建」，那是一次产品决策（涉及不可撤销的本地清空），**不在本卡范围**。**责任人：M7 收口评估。**
- **合并窗两个参数仍待真实帧分布复核**（§五 第 5 项）——该项要的是**真机帧分布统计**，
  单测层拿不到；03c 已把渲染面落地，复核动作随收口的真机走查一并做。**责任人：M7 收口。**

**下一项未阻塞任务：** **03e（确认卡四值与取消 / 打断收敛）**——批次 3 的另一半，与 03d
UI 区域不重叠（确认卡在卡片区 + 输入区，本卡在顶栏抽屉）。本卡已把「取消 / 断连分属两个
状态」（03b 落进投影）与「流式期间的禁用口径」备好。

---

### 03c · 步骤卡片流与统一智能模式（✅ 2026-09-19）

**任务编号：** T-M7-03c　**提交：** `98ac17f`

**完成范围 / 明确未做：**

- **完成：**
  - **新 `StepCardStream.tsx`**——把投影里的 `phase` / `tool_call` / `tool_result`
    渲染成可折叠的步骤列表：**进行中**（`status === null`）/ **完成**（`ok`）/
    **失败**（`error`）三态齐全；`tool_result` 只显示一行摘要（服务端本来也只发摘要，
    §8.1）。
  - **统一智能模式**：本回合没有任何工具调用时（纯文本答复 / 快档直答）步骤区
    **整体不渲染**（架构 §9.1「简单任务无步骤展示，直接给结果」）；只有 `phase:
    thinking` 的步也不单独成行（没有人类可读载荷，成行即噪音）。
  - **`AssistantPage` 挂点**：气泡内自上而下 = 步骤区 → 深度思考 → 正文；推理折叠区
    限高内滚（长推理不再把气泡撑成一屏）并改「深度思考中…」。
  - **`routing` 空态占位**：`ROUTING_PLACEHOLDER`（生产者归 P4 的 05g）——05g 一发帧
    即可显示，不必再改一次渲染；没有该帧时不会凭空多出一行。
- **明确未做（逐条对应卡面职责边界）：**
  1. **不做会话列表**（03d，另行交付）；
  2. **不做确认卡与取消**（03e）；
  3. **不改服务端**——一行未动；
  4. **不引入新事件类型**（八类不动）；也**没有改 03b 的 reducer**——三态与失败码
     03b 已经备好，本卡纯渲染。

**关键设计决策：**

| # | 决策 | 理由 |
|---|---|---|
| 1 | 失败态显示**本地映射的**用户向文案（安全枚举码 → 中文，8 个码 + 兜底） | 服务端 `tool_result.message` 是**回喂模型**的指令（「请先查询分类清单再使用完全一致的名称」，甚至「请向用户说明这一情况」），读给用户是错的；03b 的投影因此只留了安全枚举码 |
| 2 | 完成态把进度句的「正在」换成「已」 | 服务端**不发**成功摘要（§8.1 只给「成了没有」）。这是不需要扩协议就能读通的写法（「正在查询预算…」→「已查询预算…」），非「正在」开头的句子原样保留 |
| 3 | 折叠的粒度是**整条步骤流**，不是单张卡 | 单卡没有可展开的内容——`tool_result` 的载荷就是一行摘要，展开也只有一个「空」 |
| 4 | 步骤区**默认展开**，不跨轮保留 | 它存在的理由就是「长任务时让用户看见在做什么」；一轮结束气泡即卸载（投影整份释放是 03b 的既定行为），所以**步骤不落历史消息** |

**验证命令与结果：**

| 命令 | 结果 |
|---|---|
| `cd app && npx vitest run` | **81 files / 1188 passed**（开工基线 80 / 1171 → 本卡 +1 文件 / +17 用例） |
| `cd app && npx tsc --noEmit` | **0 错** |
| `cd app && npm run lint` | exit 0；本卡新增文件**零告警**（既有告警集合不变） |
| `bash ./gradlew :app:testDebugUnitTest --rerun-tasks` | 本卡未触碰 Kotlin（该行数字由 03d 的交付记录给出） |
| `cd app && npm run typecheck` | exit 0（含 `tsconfig.node.json` 一份，即 CI Client 作业的口径；比单跑 `tsc --noEmit` 更严） |
| `cd app && npm audit --audit-level=high` | 0 vulnerabilities |
| `cd server && python -m pytest tests/ -q` | 本卡未触碰 Python（同上） |

> **远端 CI：本轮按要求未确认**（推送已完成，未等待 / 未查看远端结果）——按「不得写成通过」的口径记「未确认」。Kotlin 侧（`lintDebug` / `assembleDebug` / `assembleDebugAndroidTest`）由 03d 的同一批验证覆盖，本卡未新增主源码改动。

**新增用例分布（`assistant-step-cards.test.tsx`，13 例）：** 三态齐全（含失败态）/ 未知
失败码走兜底且不透传服务端原文 / 简单任务无步骤区 / 只有 thinking 的步不成行 /
**0 帧 `delta` 的一轮**（步骤区在场、正文走占位）/ 折叠与展开 / `routing` 空态占位 /
deep 档位徽标 / 步骤区与推理折叠区、正文三者在同一气泡内互不混；外加**空 / 加载 /
失败 / 离线四态回归**四条。

**数据 / 同步 / AI / 安全影响：**

- **零写**：本卡纯渲染，没有任何写路径。
- **AI 安全**：服务端文案一律不透传（失败态走本地映射表 + 兜底）；「简单任务不展示
  步骤区」不改变任何判据，只影响可见性。
- **同步：零影响。**

**兼容与回退方式：** 纯客户端渲染，`git revert` 本提交即可。步骤区只在流式期间存在，
收尾后的历史消息形状一个字节未变（不涉及持久化，无迁移）。

**开放风险与责任人：**

- **步骤不落历史**——这一轮的步骤在收尾后即消失（投影释放的既有行为）。若收口走查认为
  需要「回看这一步做了什么」，那是一次**协议 + 存储**的改动（事件日志回读归 P3 的 04a /
  04e），**不在本卡范围**。**责任人：M7 收口评估。**
- **端到端观感未在真机走查**（同 03d 的开放风险）。**责任人：M7 收口。**
- 合并窗两个参数的真实帧分布复核仍开放（见 03d 记录末条）。

**下一项未阻塞任务：** 03d（已同批交付）。

---

### 03b · 客户端流事件消费重构与投影缓存（✅ 2026-09-19）

**任务编号：** T-M7-03b　**提交：** `1cc0fba`

**完成范围 / 明确未做：**

- **完成：**
  - **新 `app/src/features/assistant/use-assistant-stream.ts`**——流式消费从 1300 行的
    `use-assistant.ts` 拆出，按卡面要求分成两层：
    - **纯 reducer** `applyStreamEvent(projection, event)`——**八类事件全消费**。投影态含
      正文/推理增量、按 Step 归并的步骤视图（进行中 / 完成 / 失败三态 + 失败安全码）、
      `confirm` 挂起提案、`device_request`、`done` / `error` / `canceled` / `settled`。
      已 `settled` 后到达的帧一律丢弃（收尾幂等：桥侧可能在流正常结束后补发本地 `error`）。
    - **hook** `useAssistantStream`——订阅 / 退订、`streamId` 归属、收尾幂等、取消。它
      **不碰任何业务状态**，收尾时把最终投影整份交给上层回调，由 `use-assistant` 按既有
      产物分发矩阵落点。
  - **`assistant-chat-store.ts` → v2**——messages 按 `conversationId` 分桶
    （`activeConversationId` + `projections` + 顶层 `messages` 即活动会话的投影），新增
    `switchConversation` / `adoptConversation`；`clear()` 加强为清全部会话桶；
    **v1 落盘数据迁移**（`migrate`：扁平单会话历史进本地桶，**一条不丢**）。
  - **`use-assistant.ts`**——`conversationIdRef` 删除，会话 id 提升到 store；
    `settleStream` 改吃投影；新增 `cancelStream()`（`ai.cancel` **接线**——取消后三个互斥
    瞬时态收敛为空且零写）；新增 `streamProjection`（完整投影）与 `streaming`
    （其气泡视图，**`AssistantPage.tsx` 一个字符未动**）。
- **明确未做（逐条对应卡面职责边界）：**
  1. **不做步骤卡 UI**（03c）——本卡只建投影；
  2. **不做会话列表 UI**（03d）——只提供 `switchConversation` 机制与「互不串扰」的用例；
  3. **不动 Kotlin 桥**——`ai.cancel` 是 02h 已交付的命令，本卡只补调用点，**未引入新桥命令**；
  4. **三个互斥瞬时态的约束不变**——重构只搬家不换语义（v2 分发矩阵、快路径 12 s / 25 s
     体验逐字不变）；
  5. **未改** `AI_AGENT_CLIENT_ENABLED` 默认值（§五 第 4 项）。

**关键设计决策：**

| # | 决策 | 理由 |
|---|---|---|
| 1 | 投影 = **八类事件全消费**，不止 `delta` / `done` / `error` | 卡面要求「八类事件 → 投影态逐条断言」；03c 的步骤卡片流直接读 `steps`（三态齐全），不必再改一次消费路径 |
| 2 | 「活动会话的投影」放顶层 `messages`，`projections` 只放**非活动**会话 | 读当前会话仍是一次属性访问——既有 `setState({ messages })` / `getState().messages` 等约 25 处调用点与 7 个测试文件零改动；不变式由 `switchConversation` / `adoptConversation` 两个动作维护（活动会话不留副本） |
| 3 | 取消与失败**分成两个状态**（`canceled` vs `error`） | 03e 的边界：取消是用户主动、断连是被动，UI 表现与恢复路径不同，不得合并 |
| 4 | 取消**不做** `adoptConversation` | 取消 / 失败轮服务端不落库（02f 的一轮原子写入），没有可用的会话 id 语义 |
| 5 | `cancelStream` **不动** `activeTxIdsRef`（撤销窗） | 那是**上一轮已落库**那笔的入口，与本轮取消无关；顺手清掉会让用户丢掉上一轮的撤销权 |
| 6 | `cancelAgent` 在窄接口里是**可选**方法 | 能力探测仍只要求 `runAgent` + `onAiStream`（02h 口径不变）；桥缺席时取消退化为「只收敛本地态」，不会变成一条新依赖 |

**验证命令与结果：**

| 命令 | 结果 |
|---|---|
| `cd app && npx vitest run` | **80 files / 1171 passed**（开工基线 79 / 1135 → 本卡 +1 文件 / +36 用例，零失败） |
| 其中 14 个 assistant 相关文件（7 个 use-assistant/store + 5 个页面 + 2 个卡片） | **243 passed**（其中 7 个 use-assistant/store 文件 145 passed，开工基线 113 → 本卡 +32） |
| `cd app && npx tsc --noEmit` | **0 错** |
| `cd app && npm run lint` | **exit 0**；本卡新增/改动的三个文件**零告警**（既有告警集合不变） |
| `bash ./gradlew :app:testDebugUnitTest --rerun-tasks` | **406 tests / 0 failures / 1 skipped**（与 02h 记录一致；本卡未触碰 Kotlin） |
| `cd server && python -m pytest tests/ -q` | **exit 0**：**1075 passed / 37 skipped**——其中 03a 的在途用例已随 `985ee2d` 落地（1029 → 1075 的 46 条是 03a 的），故本行只作「无回归」的证据，**不作本卡基线**；见下方「并发会话说明」 |

**新增用例分布：**
- 新 `assistant-stream-projection.test.ts`（18）——reducer 纯函数：八类事件逐类落进投影、
  `delta` 按 `channel` 分流、收尾幂等（`done` 先到 / `error` 先到）、畸形帧丢弃、
  **0 帧 `delta` 的一轮**、纯函数性（不改入参）；
- `use-assistant-agent-stream.test.ts`（13 → 20）——投影吐给上层、**0 帧 delta 照常收尾**、
  `conversation_id` 落 store、**会话切换互不串扰**（切回来原样、不夹带别会话）、
  **取消（有活动流 / 无活动流）三态收敛且零写**、半截文本保留并标注取消；
- `assistant-chat-store.test.ts`（9 → 19）——切换 / 采纳 / 清空 / 跨模块重载恢复 /
  **v1 → v2 迁移**（含畸形载荷与 200 条上限）。

**并发会话说明（数字归因）：** 本卡开工期间，**另一会话正在 03a（服务端面）的在途改动**
（`server/src/models/ai.py`、`modules/ai/conversation.py`、`routes.py`、`schemas.py`、
`tests/test_migrations.py` 均为未提交状态）。因此：
- **app 侧数字是本卡基线的可靠对照**（本卡是唯一改 app 的会话）；
- 服务端 pytest 的数字**必然含 03a 的在途用例**，不能作为本卡的基线对照——本卡
  **未触碰任何 Python 文件**，不存在由本卡引入的服务端回归；
- 提交用 `git commit --only <本卡文件>`，**不夹带** 03a 的在途改动。**唯一的例外**：本文件
  （P2 计划）是两卡共写的，`--only` 只按**路径**隔离，故本卡的提交里带上了 03a 写的
  §一「03a ✅ 完成」那一行——**内容未改，归属仍是 03a**；
- **数字口径**：app 侧全量在 `1cc0fba` 定稿前为 **1170**（03a 的 §六 记录引用的是那一版）。
  本卡末条 reducer 用例（修复 `tool_calling` 的 phase 帧会把该步推理档位清成 null 的
  缺陷）补入后为 **1171**，**以本卡上表为准**。

**数据 / 同步 / AI / 安全影响：**
- **零写路径不变**：本卡新增的取消路径与流式收尾不触发任何写命令（用例逐条断言
  `insertTransaction` / `updateTransaction` / `softDeleteTransaction` 零调用）。
- **AI 安全**：流的错误文案仍不透传（安全口径不变）；`pending-confirm` 的确认门未被绕过
  （既有用例仍绿）。
- **持久化**：localStorage 同 key（`aozora-assistant-chat`）升 v2，旧数据自动迁移；登出
  `clear()` 语义**加强**为清全部会话桶（跨账号串会话的面反而更小）。

**兼容与回退方式：**
- 灰度为既有双层开关（服务端 `ai_agent_enabled` + 客户端 `AI_AGENT_CLIENT_ENABLED`），
  本卡未改默认值；关闭时整条回落既有单发链路。
- 回退：纯客户端重构，`git revert` 本提交即可。持久化 v2 → v1 时旧代码读同一 key 会因
  `version` 不匹配而回退空态（历史不显示但**不报错**）；v1 数据只多了会话维度，无破坏性。

**开放风险与责任人：**
- **端到端真机路径至今一次没跑过**（02h / 02i 的共同缺口）——本卡的全部验证仍停在单测层。
  开关转 true 之前须先在模拟器上跑通多步任务 / 挂起提案 / 失败收尾三态。**责任人：M7 收口。**
- 03d 接 `switchConversation` 时须定「删除当前会话后的回退目标」与 `book_id` 联动规则，
  以及**流式期间是否允许切换**：本卡（收口）已防住「迟到的 `done` 把用户拽回旧会话 + 删掉
  目标会话缓存」，但一轮的消息**仍会落到切换后的会话桶里**——根治要把一轮绑到发起时的
  会话（`setMessages` 的十几处调用点都要带目标），属 03d 的设计选择题。
- 03e 接 `cancelStream` 时须定「停止生成」按钮的可见条件，以及断连（被动）与取消（主动）
  的提示差异——本卡已在投影里把两者分开（`canceled` vs `error`），但 UI 表现未定。
- **★ 陈旧 `conversation_id` 的 404 降级契约未实现**（03a 在 §五 第 1 项交办 03b / 03d；
  **卡面验收不含此项，且改动要落到 Kotlin 桥——越出本卡「不动 Kotlin 桥」的边界，故未做**）。
  已核实的事实链，供 03d / 收口直接落地：
  1. 服务端对「已删除 / 不是本人的 / 不存在」的 id **一律 404 `conversation_not_found`**
     （`conversation.py:load_owned`），且是在 `POST /ai/agent` **开流之前**抛出——所以它不会
     变成 `error` 帧，只走桥的失败路径；
  2. **桥把 404 折叠掉了**：`AiGatewayClient.kt:317` 的 `mapStreamFailure` 只特判
     401/429/503，其余（含 404）一律 `AiError.Network("Server error: HTTP $code")`——
     JS 侧看到的 code 是 **`network`**，与普通网络失败**不可分辨**；
  3. `useAssistantStream` 的桥层失败分支按 P1 原样收尾（`settled` 置真、`error` 留 null），
     **失败码没有传下去**；
  4. 于是**下一轮仍带同一个 id → 再 404**。P1 时 id 活在内存 ref（切页即丢）所以没暴露，
     **本卡把它提升到 store 之后这个状态变成持久的**。
  **要求的终态**：拿到该失败时丢弃陈旧 id（等价于切回本地会话），下一轮开新会话。
  **前置**：给 404 一个自己的错误码（改 Kotlin 桥，或在 03d 里按 message 里的
  `HTTP 404` 兜底匹配——前者干净、后者不碰桥）。**必须在 03d 上线删除入口之前补齐。**
  **责任人：03d / M7 收口。**
- 合并窗两个参数（100 ms / 同通道同 Step）仍待真实帧分布复核（§五 第 5 项，落 03c / 收口）。

**收口（2026-09-19，`0c99dbc`）——对抗式自审抓出两处并修掉：**

两处都不是「测试没写够」，而是**本卡自己的改动引入的**：一处把并发假设寄生在别的模块上，
一处是多会话分桶后新出现的状态迁移。逐处都做了**回退实验**（变异注入 → 新用例变红 →
按 sha256 逐字节还原），确认用例真的盯住了修复点。

| # | 缺陷 | 触发路径 | 修法 | 变异复核 |
|---|---|---|---|---|
| 1 | `start` 的「上一轮兜底退订」路径下，**上一轮迟到的桥层 reject 会把新一轮收尾掉** | 今天不可达（`submitInput` 的 `isSubmittingRef` 挡着），但那是**另一个模块**的假设——本模块的注释还写着「防流未收尾就又起一轮」，即自己承认这条路存在。03e 接打断交互后必然踩 | `roundRef` 轮次序号：`onEvent` 与 catch 都比对「我还是不是当前轮」 | 去掉比对 → `上一轮迟到的桥层失败不收尾新一轮` 变红 |
| 2 | `adoptConversation` 会把用户**拽回**旧会话，并**删掉目标会话的缓存** | 03d 的会话列表允许流式期间切换时：本地会话发出的一轮在流中切到 conv-B，`done` 交回 conv-A → `activeConversationId` 被改回 conv-A（用户被弹走），`projections['conv-B']` 被删（**缓存丢失**） | 只在 `activeConversationId === null`（本地会话刚拿到 id）时采纳；否则不动任何状态。**对所有今天可达的路径行为等价**（服务端只在客户端没带 id 时新建会话，带了必回同一个 id） | 去掉早退 → 两条 adopt 用例（store 级 + hook 级）变红 |

**收口后验证：** 本卡两个测试文件 **43 passed**（收口前 39，+4 条：adopt 两条、轮次归属两条）；
`npx tsc --noEmit` 0 错；`npm run lint` exit 0 且本卡三个文件零告警。
**全量 81 files / 1188 passed 含 03c 的在途用例（`assistant-step-cards.test.tsx`，未提交），
不作本卡基线**——本卡自己的增量是 39 → 43。

**明确未做（收口也不越界）：** 流式期间切换会话时，**这一轮的消息仍会落到「切过去之后」的
会话桶里**（上面第 2 处只防住了「拽回 + 丢缓存」，没有把一轮绑到发起时的会话）。要根治得让
「一轮」持有自己的 bucket（`setMessages` 的十几处调用点都要带会话目标），那是 03d 的选择题：
**要么禁止流式期间切换，要么把一轮绑定到发起会话**。已记入下方开放风险。

**下一项未阻塞任务：** **03c（步骤卡片流与统一智能模式）**——本卡已把
`streamProjection.steps` 备好（进行中 / 完成 / 失败三态 + 失败安全码），03c 只需渲染。
03a 与本卡同批（批次 1），批次 2 起可直接开工。

---

### 03a · 会话索引服务端化与事件读取 API（✅ 2026-09-19）

**任务编号：** T-M7-03a　**提交：** `985ee2d`

**完成范围 / 明确未做：**

- **完成：**
  - **模型**（`server/src/models/ai.py`）——`AIConversation` 补三列：`title`（`String(120)`，
    服务端按首轮输入派生）、`last_active_at`（**列表排序键**）、`archived_at`（软删，与
    `Category` / `SavingsGoal` 同口径），外加复合索引 `ix_ai_conversations_user_archived_active`
    （列表查询的形状：本人的 / 未归档的 / 按活跃倒序）。
  - **迁移** `server/src/migrations/versions/0019_conversation_index.py`（紧接 0018；
    只加列 + 索引，无回填、无数据迁移）。
  - **服务**（`server/src/modules/ai/conversation.py`）——四个新方法
    `list_conversations` / `rename` / `delete` / `events(after_seq)`；归属加载抽成
    `load_owned`（`resolve` 复用），**已归档行与跨用户行同码**；`append_turn` 写成功后
    更新索引（`_note_activity`：标题只在还空着时派生，活跃时间每轮刷新）。
  - **端点**（`routes.py` + `schemas.py`）——`GET /ai/conversations`、
    `PATCH` / `DELETE /ai/conversations/{id}`、`GET /ai/conversations/{id}/events?after_seq=`；
    四个端点与既有 AI 端点同用 `require_active` 守卫（冻结账号一律 403）。
  - **测试** `server/tests/test_ai_conversations_api.py`（44 例）+ `tests/test_migrations.py`
    补 0019 的链断言与列 / 索引级断言。
  - **文档** `docs/architecture/ai-architecture.md` 新增「会话管理端点」一节（含登出粒度结论）；
    `production-execution-plan.md` 本卡行的验收列回填交付摘要。
- **明确未做（逐条对应卡面职责边界）：**
  1. **不做客户端**——03b / 03d 的面，一行未碰；
  2. **不改 `/ai/agent` 的事件协议**——八类事件一个不多一个不少；
  3. **不做完整事件日志**——`events()` 只按既有 `type` 原样读出，`ignorable` / Surface
     子集归 P3 的 04a；
  4. **不实现摘要**——`summary` 保持空串（归 04c）；
  5. **不做保留期**——到期物理清理由 04d 负责；本卡只保证「用户主动删除」这条路径上
     正文立刻消失。

**关键设计决策：**

| # | 决策 | 理由 |
|---|---|---|
| 1 | **删除 = 归档行 + 删事件正文** | 行留着是 `ai_usage.conversation_id` 的锚点（硬删会把它 `SET NULL`，破坏审计链），且与 04d「保留期删事件、不删会话行」同构；正文没有留的理由——用户主动删除后服务端不该再持有它 |
| 2 | **`last_active_at` 不复用 `updated_at`** | 后者被重命名 / 软删 / 换账本一并改动，列表顺序会莫名跳位；「重命名后列表顺序不变」有用例钉住 |
| 3 | **已删除 / 跨用户 / 不存在 = 同一个 404** | 读取面的唯一防线（02f 口径），且给客户端一条幂等重试路径（重试删除拿到 404 即「已经不是我的会话」） |
| 4 | **`events(after_seq)` 是严格大于** | 客户端拿上一批最后一条的 `seq` 再拉一次不会重复收到它——断线续读的语义 |
| 5 | **登出保持按用户粒度清，不引入设备维度**（P1 挂账 1 的结论） | `ai_conversations` 没有设备列、`/ai/agent` 也不带 `device_id`；加设备维度要同时改请求契约与 02h 已冻结的桥，而 P2 不做跨设备正文同步（04e 决策门）。代价：多设备下另一台机器的服务端会话会消失——客户端把 404 当「已不是我的会话」并开新会话（交 03b / 03d） |
| 6 | **列表不分页** | 会话数由用户创建量决定，且每个会话另有轮数上限；侧栏本来就要全量 |

**验证命令与结果：**

| 命令 | 结果 |
|---|---|
| `cd server && python -m pytest tests/ -q` | **1075 passed / 37 skipped**（开工基线 1029 + 本卡 46：新文件 44 + 迁移 2） |
| `cd server && python -m ruff check src/ tests/` | All checks passed |
| `cd app && npx tsc --noEmit` / `npx vitest run` | 0 错 / 1170 passed（03b 会话交付后的 app 侧数字；**本卡未触碰任何 app 文件**） |
| `bash ./gradlew :app:testDebugUnitTest --rerun-tasks` | 406 tests / 0 failures / 1 skipped（同上，本卡未触碰 Kotlin） |

**回退实验（11 处变异，逐条回退后实测）：11/11 变红** —— 列表不滤软删（2 例红）、列表不按
用户过滤（1）、排序倒置（4）、取会话不校验归属（3）、取会话不看软删（4）、删除不清事件（1）、
删除不置归档位（4）、重命名也刷活跃时间（1）、`after_seq` 改 `>=`（1）、标题被每轮覆盖（2）、
标题不截断（1）。

**数据 / 同步 / AI / 安全影响：**

- **同步：零影响**——不进 sync 协议，无 `sync_changes` 写入，客户端一行未动。
- **AI：** 新增四个管理端点；agent 路径行为不变——`resolve` 只多一条「已归档 → 404」的过滤
  （旧 id 被删除后不可复用，这正是不变量）。每轮多一次会话行 UPDATE（title / 活跃时间）。
- **安全：** 会话列表是**新的读取面**，三条硬约束都有真实用例：列表只回本人、
  rename / delete / events 三个端点的跨用户一律 404、删除后旧 id 在四个面上都是 404；
  登出粒度结论已入档（`ai-architecture.md`）。
- **隐私：** 用户主动删除会话会**立即清掉正文事件**，不等保留期。
- **删号**（`infra/cleanup/account-deletion.py`）沿 P1 的清单按用户清理会话与事件表，
  本卡未改动——与登出同粒度，和 §五 第 1 项的结论一致。

**兼容与回退方式：**

- **纯增量**：四个新端点；既有端点与客户端行为不变（旧客户端不调用即零影响）。
- **迁移**：只加列 + 索引，无回填。存量会话升级后 `title` 为空串（客户端兜底文案渲染）、
  `last_active_at` 为 NULL（列表按 `created_at` 兜）——都不影响可读性。
- **回退** = revert 本提交；`0019` 的 `downgrade` 可逆（drop 索引 + 三列）。

**开放风险与责任人：**

- **存量会话没有标题**——0019 不回溯派生（那要读事件表），升级后旧会话在列表里是空标题，
  由客户端兜底文案渲染。**责任人：03d。**
- **多设备误伤（挂账 1 的残余）**——粒度结论已定（按用户清），但「客户端把 404 降级为
  开新会话」这一半**还没实现**。**责任人：03b / 03d。**
- **会话列表不分页**——单账号会话数极大时列表会很长；当前没有实测分布，收口时按真实使用
  复核（是否要 limit / 游标）。**责任人：M7 收口。**
- **两个开关的默认值未动**（服务端 `ai_agent_enabled` / 客户端 `AI_AGENT_CLIENT_ENABLED`）。

**下一项未阻塞任务：** **03c（步骤卡片流）** 与 **03d（多会话列表，对接本卡四个端点）**
——03a 与 03b 同批（批次 1），批次 2 起可直接开工。

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