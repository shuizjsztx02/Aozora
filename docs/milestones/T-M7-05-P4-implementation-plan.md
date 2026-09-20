# T-M7-05（P4）实施推进计划

**建立：** 2026-09-19
**关系：** 本文件是 **T-M7-05（P4：全功能覆盖——域路由、Skill 与设备能力通道）的执行细则与进度看板**。任务边界、交接产物与验收标准以 `docs/milestones/production-execution-plan.md` M7 章节为准；本文件**不改变任何验收标准**，只细化推进方式、逐卡职责边界，并记录中间结果。
**架构与决策：** `docs/architecture/ai-agent-architecture.md`（§3 功能全覆盖盘点与映射、§4.3 设备能力通道、§7 工具系统、§12.2 未决问题）、`docs/architecture/ai-architecture.md`（当前契约）
**上游：** `docs/milestones/T-M7-02-P1-implementation-plan.md`（P1）、`T-M7-03-P2-...`（P2）、`T-M7-04-P3-...`（P3）——本文件 §五 承接上游挂账项
**用法：** 领取任一张子卡的 Agent，先读本文件对应小节，再读 `production-execution-plan.md` 该卡的验收列。

---

## 一、状态看板

| 卡 | 内容 | 状态 | 完成日 | 提交 | 备注 |
|---|---|---|---|---|---|
| **05a** | ActionProposal v2 契约（文档/协议先行） | ✅ 完成 | 2026-09-19 | `ab7b1c3` | 批次 1；v1 文件一字未改，双读车道判定＝`contractVersion` 在场与否；服务端接线归 05b（见 §六） |
| **05b** | 全量工具：记账与账户账本域 | ✅ 完成 | 2026-09-20 | `733379e` | 批次 2；含 05a 交接的**服务端接线**（校验器分流 + execute 放行 + payload 授权 + 读协商字段）；6 域 / 27 个工具；v1 车道仍 = P1 的 15 个 |
| **05c** | 全量工具：预算存钱、定时、角色域 | ⏳ 未开始 | | | 批次 3 |
| **05d** | 全量工具：分析与其余服务端域 | ⏳ 未开始 | | | 批次 4 |
| **05e** | 设备能力通道：服务端协议与 loop 暂停/恢复 | ✅ 完成 | 2026-09-19 | `ec05120` | 批次 2；服务端侧全部交付，客户端执行器归 05f；开关默认关闭（`AI_DEVICE_CHANNEL_ENABLED`） |
| **05f** | 设备能力通道：客户端执行器与设备工具接线 | ⏳ 未开始 | | | 批次 3 |
| **05g** | 域路由与预路由 | ⏳ 未开始 | | | 批次 5 |
| **05h** | Skill 封装与按需加载 | ⏳ 未开始 | | | 批次 4 |
| **05i** | 帮助文档检索（**可选，可砍**） | ⏳ 未开始 | | | 批次 5 |
| **收口** | P4 四板 + 覆盖度清单逐项走查 | ⏳ 未开始 | | | 批次 6 |

**状态取值：** ⏳ 未开始 / 🔄 进行中 / ✅ 完成 / ⛔ 阻塞（阻塞须在本表备注列写明原因与解除条件）

---

## 二、批次与依赖

```
批次 1  05a ─┬─→ 批次 2  05b ─→ 批次 3  05c ─→ 批次 4  05d ─┐
             └─→ 批次 2  05e ─→ 批次 3  05f ────────────────┼─→ 批次 5  05g ─┐
                            批次 4  05h ────────────────────┘   批次 5  05i ─┴─→ 批次 6 收口
```

| 批次 | 卡 | 可并行 | 说明 |
|---|---|---|---|
| 1 | 05a | — | 契约是硬前置：写工具全量化的前提 |
| 2 | 05b、05e | ✅ 不同文件面 | 05b 改 `tools.py`；05e 改 `agent.py` / `stream.py` / `routes.py` |
| 3 | 05c、05f | ✅ 服务端 / 客户端 | 05c 依赖 05b 的注册表范式；05f 依赖 05e 的协议 |
| 4 | 05d、05h | ✅ 互不依赖 | 05d 是工具域；05h 是 prompt/Skill 面 |
| 5 | 05g、05i | ✅ 互不依赖 | 05g 需全量工具就位才有「域」可选；05i 可选 |
| 6 | 收口 | — | 四板 + 覆盖度清单逐项走查 |

**为什么拆成子任务：** 原卡跨**五个面**——契约（跨 TS / Python / schema 三方）、服务端工具全集（P1 已交付 15 个，全量约 79 条桥命令对应的表单任务）、**设备能力通道（跨服务端 + Kotlin + TS 三层）**、域路由、Skill 封装，任一项卡住则整卡不可交付、无法独立验收。按 `docs/engineering/需求变更规范.md` §五.1（文档/协议 → 数据与服务 → 客户端能力 → UI → 联调）拆分。

**跨期提前开工（唯一的安全切分点）：** **05a（契约）与 05e 的协议部分不依赖 P2 / P3**——ActionProposal v2 是纯契约工作，设备通道协议只依赖 P1 已交付的 SSE 事件。若想提早并行，这两处可在 P2 期间开工，且越早越好（契约晚改＝已实现的工具返工）。**其余各卡必须等 P3 收口**（工具全量化要等服务端上下文管理的稳定形态）。

**外部阻塞：** 05c 依赖 **T-M7-01 已交付的三实体上云**（`savings` / `scheduled` / `personas`）——已满足。

---

## 三、逐卡细则

> 每卡均含「内容 / 职责边界 / 产出 / 验证」。**职责边界是防范围蔓延的关键，不得越界**。

### 05a ActionProposal v2 契约（文档/协议先行）

- **内容：** 把 ActionProposal 从 v1 的最小 action 集（`add_tx` / `add_tx_batch` / `query` / `report` / `create_category` / `config` / `chat`，见 `docs/APP-CONTEXT.md` §6.3）升为 **v2 覆盖全量 CRUD**。**按 L3 变更流程文档先行**：先改 `docs/architecture/ai-architecture.md`（现仅 30 行的薄指针文档，需实质扩写），再改 `contracts/action-proposal/schema.json`，再同步**三方校验器**——TS（`app/src/domain/action-proposal.ts`）、Python（`server/src/modules/ai/validator.py`，86 行）、JSON Schema——并补 v2 的有效 / 无效 fixtures（现 16 个 fixture），复用既有三方一致性测试范式（`server/tests/test_ai_proposal_parity.py`）。**v1 兼容**：schema 版本协商或双读，旧 fixture 与旧客户端行为逐字不变。
- **职责边界：** **只做契约**——不实现任何工具（05b/05c/05d）、不做设备通道（05e）。**不放松三条铁律**（模型永不输出 UUID、金额由确定性代码算、写库必经校验与审批门）。
- **产出：** `docs/architecture/ai-architecture.md`、`contracts/action-proposal/`（schema + fixtures）、`app/src/domain/action-proposal.ts`、`server/src/modules/ai/validator.py`、parity 测试
- **验证：** **三方一致性**——同一 fixture 在 schema / TS / Python 三处结论一致（P4 出口标准）；v1 的全部 fixture **仍全部通过**；v2 新 action 枚举与 `tools.py` 的 `proposal_action` 取值无冲突；旧客户端不因契约扩展而行为变化；必要的 ADR（若契约版本策略需固化取舍）。

### 05b 全量工具：记账与账户账本域

- **内容：** 把**记账域**（`transaction.*` / `category.*`）与**账户账本域**（`account.*` / `book.*` / `meta_set(默认项)`，架构 §3.2）从 P1 的只读/部分能力**扩到全量 CRUD**。沿用既有注册表结构（`Tool` dataclass：`name` / `domain` / `kind` / `description` / `parameters` / `handler` / `proposal_action`，`server/src/modules/ai/tools.py:243`），写工具经 05a 的 v2 提案产出。**每域工具数 ≤8 的约束下按操作分组**（架构 §7.2）；**工具名一律下划线**（`PROVIDER_TOOL_NAME_PATTERN`，点号会让 provider 直接 400——02c 收口实测）。
- **职责边界：** **不做设备类**（`icon.pick` 归 05f）、**不做预算 / 定时 / 角色**（05c）、**不做分析与其余域**（05d）。**不做域路由**（05g）。**description 是模型可见的唯一语义来源，须人工审核，不得机器直译。**
- **产出：** `server/src/modules/ai/tools.py`（扩展）、`server/tests/test_ai_tools_*.py`
- **验证：** 每域工具数 ≤8；schema 与 v2 契约枚举无冲突；未知工具名走降级契约（不抛异常）；写工具一律走审批门（02e 路径不放开）；**真实调用抽样**记录工具选择正确率（R-PROD-17 数据点）；读工具结果**不含 id**、草案含 id 但回喂前必须剔除（02c 边界）。

### 05c 全量工具：预算存钱、定时、角色域

- **内容：** **预算存钱**（`budget.*` + `savings.*`）、**定时**（`scheduled.*`）、**角色**（`persona.*` + `meta_set(当前角色)`）三域的读写工具。后两者依赖 **T-M7-01 已上云**的三实体（`savings` / `scheduled` / `personas`），此前它们「仅本地」、服务端 loop 够不到（架构 §3.1）。
- **职责边界：** **不做记账 / 账户账本**（05b）、**不做分析与其余域**（05d）。**不复用 05b 的卡面**——三域各自 5–8 个工具的约束独立成立。
- **产出：** `server/src/modules/ai/tools.py`（扩展）、测试
- **验证：** 三域工具数各 ≤8；存钱 / 定时 / 角色的写操作经 v2 提案与审批门；离线语义（这三类实体的本地副本）在工具路径上的行为有明确结论；真实调用抽样正确率记录。

### 05d 全量工具：分析与其余服务端域

- **内容：** **分析域**（`/stats/*`、`/ai/report`、本地聚合的封装，读）、**导出 / 反馈 / 隐私同意**（`export.*` / `feedback.*` / `consent.*`）、**安全账号**（`auth.*`、删号）、**同步**（`sync.getStatus` / `trigger`）。架构 §3.2 覆盖度清单的剩余行。
- **职责边界：** **删号类工具是最高危面**——必须最严审批（不可自动执行），且**不得**绕过既有的账号删除流程（`infra/cleanup/account-deletion.py`）。**不做域路由**（05g）。
- **产出：** `server/src/modules/ai/tools.py`（扩展）、测试
- **验证：** 架构 §3.2 中本卡负责的各行逐项可达；删除类工具强制确认且有守护用例；导出 / 反馈等写操作经审批门；**对账不变量全绿**（分析与统计类工具属数据正确性面，`docs/APP-CONTEXT.md` 10.1）。

### 05e 设备能力通道：服务端协议与 loop 暂停/恢复

- **内容：** 给 `device_request` 装上**第一个生产者**——现状是 `stream.py` 明写「`device_request` **无生产者**」（八类事件里唯一没有发射路径的一类）。实现架构 §4.3 的**两类**协议：
  - **单向指令**（刷新小组件、触感、分享文本）：loop 产出设备指令 → SSE 下发 → 客户端执行 → **不回传**；
  - **需结果**（权限申请、语音转写、图片选择）：loop **暂停** → SSE 下发 → 客户端执行 → **HTTP 上行回传** → loop 继续。需新建回传端点。
  并处理：暂停期的超时与取消、暂停态的存储（架构 §8.4 的单 worker 内存态假设）、`AgentObserver` 的扩展钩子、`StopReason` 是否需要新增取值。
- **职责边界：** **不做客户端执行器**（05f）。**不改八类事件本身**——`device_request` 已在八类内，本卡只是补生产者与回传通道。**不做域路由**（05g）。
- **产出：** `server/src/modules/ai/agent.py`、`stream.py`、`routes.py`、`schemas.py`、测试
- **验证：** 单向指令发出即继续、不等待回传；需结果类可暂停并被上行回传唤醒；**回传超时收敛到一致状态**（架构 §4.4 不变式 3）；取消后无悬挂设备请求；客户端断连时设备请求的处理有明确结论（与 P3 的 04b 恢复语义对齐）。
- **⚠️ 文档/注释不一致（本规划核对发现，须在本卡一并修正）：** `server/src/modules/ai/stream.py:34` 与 `:72` 把设备能力通道标注为「归 **P2**（架构 §4.3）」，与架构 §10 迁移路径及本规划（**P4**）不符。属 P1 写作时的笔误，本卡改到该文件时顺手改正。

### 05f 设备能力通道：客户端执行器与设备工具接线

- **内容：** 把**已存在**的设备类桥命令族（当前 79 条命令中的设备部分：权限 `permissions.*`、语音 `voice.*`、图片 `image.pick` / `image.parse`、分享 `share.text`、触感 `haptic.feedback`、提醒 `reminder.*`、小组件 `widget.refresh`、图标 `icon.pick`、主题背景 `theme.pickBackground`）接成**设备工具执行器**。新增桥命令（`device.execute` / `device.result` 或等价命名）——**命令数 79 → N，四处同步**：`BridgeProtocol.kt` 的 const 与 `ALL`、`AozoraBridge.dispatch`、`bridge-types.ts`、`native-bridge.ts`，且 `SecurityConfigTest.kt:47–57` 的硬编码计数断言（现值 `79`）同步更新。事件分发复用既有的 `window.__aozoraAiStream__`（`AozoraBridge.kt:1327` / `native-bridge.ts:516`）与「先存 prev → 新函数内 listener + 调 prev」的链式包装范式（`onPermissionResult`，`native-bridge.ts:563–583`）。需结果类实现 HTTP 上行回传。
- **职责边界：** **不做服务端协议**（05e）。**Token 不出 Kotlin 层**（ADR-003 不变）。**不复用 / 不改写** `use-assistant` 现有的直接桥调用路径（那是本地表单路径；两条路径不得互相改写）。
- **产出：** `app/src/main/java/com/aozora/app/bridge/*`、`app/src/main/java/com/aozora/app/ai/*`（如涉及）、`app/src/platform/bridge/*`、`app/src/features/assistant/*`、`SecurityConfigTest.kt`
- **验证：** 命令计数断言与四处清单一致；TS 命令完整性契约测试；设备类工具**端到端可达**（提醒 / 权限 / 分享 / 小组件 / 图标，P4 出口标准）；需结果类回传后 loop 继续；权限被拒 / 设备不可用时的降级有明确结论；`./gradlew :app:testDebugUnitTest` 全绿。

### 05g 域路由与预路由

- **内容：** 落地架构 §12.2 **已定第 2 项**：「域路由 P1 不做，归 P4（域扩展到全量工具时才需要）」。方案对比（规则 / 轻量模型）后定实现；`phase: routing` 的生产者接通（`stream.py` 现注释「`routing` 属域路由，**P4 才有**」，P2 的 03c 只留空态占位）；与工具注册表的 `tools_for_domains(domains)`（`tools.py:1575`，按域过滤注入）对接。**准确率实测**（R-PROD-17）。
- **职责边界：** **不做工具定义**（05b–05d）。**不改入口推理分级**——那是 02d 的档位判定（决定**推理开销**），域路由决定**注入哪些工具**，架构 §12.2 明确标注二者是**易混淆的两件事**，不得合并实现。
- **产出：** `server/src/modules/ai/`（新路由模块）、`agent.py` 接线、测试
- **验证：** 误路由率 / 漏注入率**实测记录**（R-PROD-17 的下一步）；路由失败时的兜底（全量注入或单域）有明确策略与用例；简单输入不因此增加模型调用（复核架构 §12.2 已定第 2 项的口径）；`routing` 事件在步骤卡上可见（P2 03c 的占位接通）。

### 05h Skill 封装与按需加载

- **内容：** 复用既有 `PromptTemplate` 机制（`server/src/modules/ai/prompts.py`，428 行）封装 Skill，**按需加载**而非常驻上下文；定义 Skill 与工具域的配合关系；加载条件与 token 预算。
- **职责边界：** **不改工具定义**（05b–05d）。**不动快路径的 prompt**（PR-2 的 12 s / 25 s SLO 不受影响）。
- **产出：** `server/src/modules/ai/prompts.py` / 新 skill 模块、测试
- **验证：** **「新增一个表单能力不改 prompt 主体」的验证用例**（P4 出口标准，本卡是其实现方）；Skill 按需加载不常驻上下文（token 实测）；未命中 Skill 时行为与 05b–05d 逐字一致。

### 05i 帮助文档检索（可选，可砍）

- **内容：** 仅限**产品帮助 / 理财知识**的最小检索能力（架构 §3.2 之外的可选项，P4 原范围标注「可选」）。
- **职责边界：** **若 P4 出口不纳入本项，本卡直接关闭**并在 `production-execution-plan.md` 与架构文档标注「不做」，不得默默留着造成范围含糊。

### 收口（批次 6）

- P4 四板全绿（见 §四）
- **功能覆盖清单（架构 §3.2）逐项走查**——每一行「对话可完成」都要有可复现的走查记录
- 设备类能力（提醒 / 权限 / 分享 / 小组件 / 语音 / 图片 / 图标）对话内可达
- **ActionProposal v2 三方一致性**通过、**v1 契约与旧客户端兼容**
- P4 出口标准逐条核对（`production-execution-plan.md` T-M7-05 验收列）

---

## 四、推进机制

### 提交规则

- **每卡一次独立提交**，**逐文件精确 add**（`git add <本卡文件>`），**绝不 `git add -A`**。**并发会话下用 `git commit --only <路径>`**。
- 跨卡共享文件（`tools.py`、`agent.py`、`stream.py`、`BridgeProtocol.kt`、`native-bridge.ts`）由**后完成**的卡负责解决冲突。**`tools.py` 是 05b / 05c / 05d 三卡的串行瓶颈**——已按批次 2 / 3 / 4 排开。

### 验证基线（每卡完成后必跑）

```
cd server && python -m pytest tests/ -q          # 每卡只允许在基线上增加，不得减少
./gradlew :app:testDebugUnitTest --rerun-tasks   # gradlew 在仓库根目录，须从仓库根运行
cd app && npx vitest run
cd app && npx tsc --noEmit                       # 0 错
```

> **基线数值以开工时实测为准，不在本文件写死。** 参考：02g-2 交付后（2026-09-19）服务端 1029 passed + 37 skipped / JVM 394 tests / vitest 1107 passed / tsc 0 错；P2 / P3 完成后须重新实测。
> ⚠️ **多会话陷阱（继承自 P1 计划 §四）：** 并发会话下跑全量套件的数字**不可作为本卡基线**，须以 `pytest <本卡测试文件>` 的增量与 `--collect-only` 为准。
> ⚠️ **桥命令计数是四处硬编码一致性面**（`BridgeProtocol.kt` 的 const 与 `ALL`、`AozoraBridge.dispatch`、`bridge-types.ts`、`native-bridge.ts` + `SecurityConfigTest.kt` 的断言）——05f 动它时四处 + 断言必须同一次改完，否则测试直接红。

### 阻塞处理

- 卡内发现需改**架构决策**时**停下来先改文档**再动代码（`docs/engineering/需求变更规范.md` 对 AI 协议类改动的硬要求；05a 本身就是文档先行卡）。
- 出现阻塞：本文件 §一 状态看板改为 ⛔ 并写明「原因 + 解除条件」，同时更新 `docs/risk-register.md`。
- 不替其他卡修问题，不删别人的临时产物。

### 卡完成时的记录义务

每卡完成后，在本文件 §六 追加一条中间结果记录，按 `production-execution-plan.md` 末尾的**交接模板**填写。

---

## 五、承接自 P1 / P2 / P3 的挂账项

| # | 挂账 | 来源 | 落卡 |
|---|---|---|---|
| 1 | **工具全集归 P4**——约 79 条桥命令对应的工具集（P1 只交付 15 个 / 3 域）。**记账域与账户账本域已闭环（05b）**：15 → 27 个工具 / 6 域；预算存钱与定时角色归 05c、分析与其余服务端域归 05d | 02c 职责边界 | **05c / 05d** |
| 2 | ~~**`device_request` 无生产者**~~——**服务端侧已闭环（05e，`ec05120`）**：两类协议 + 首个生产者 + 上行端点；残留只剩客户端执行器与渲染路径 | `stream.py:34,72` | **05f** |
| 3 | **`routing` 阶段无生产者**——`stream.py` 现注释「属域路由，P4 才有」 | `stream.py:30` | **05g**（+ P2 03c 的占位） |
| 4 | **域路由归 P4**（架构 §12.2 已定第 2 项；P1 的「快路径」由 loop 自然产生，不额外路由） | 架构 §12.2 | **05g** |
| 5 | **R-PROD-17（工具集过大降低选择准确率）**——`tools.py` 的域分组已就位。**05b 已交首个 27 工具的真实抽样**（首调 70% / 两步 93% / **零例选错写工具**，`server/scripts/probe_tool_selection.py` 可重跑）；**评估分组策略与预路由准确率**仍是 05g 的下一步 | `risk-register.md` | **05g**（+ 05c / 05d 的抽样） |
| 6 | **`icon.pick` / 设备类能力**属设备能力通道 | 架构 §3.2 映射表 | **05f** |
| 7 | **`ai_agent_step_timeout_seconds=20` 是初值**（02d 实测深档单步 5–7 s，真实分布待统计） | 02g / 02g-2 §五 | **收口**（全量工具与设备通道接上后按真实分布校准） |
| 8 | **会话级配额数值（50 轮 / 500k token）与合并窗参数均为初始值** | 02f / 02g-2 §五 | **收口**（按真实使用分布校准） |
| 9 | **R-PROD-22 残余：经真实 nginx 的 SSE 端到端确认** | 02g §五 | **收口 / 部署执行者**（P4 覆盖度走查时一并确认） |

---

## 六、中间结果记录

### 05a ActionProposal v2 契约（2026-09-19，`ab7b1c3`）

```text
任务编号：T-M7-05a（P4 批次 1）
完成范围 / 明确未做：
  完成——契约三件套（schema-v2 / TS 校验器 / Python 校验器）、v1↔v2 别名表、
  17 个 v2 fixture、三方一致性用例（TS 56 例 / Python 30 例）、
  ai-architecture.md 实质扩写、ADR-009、D-PROD-15。
  未做（刻意，非遗漏）——① 服务端接线：/ai/validate 仍走 v1 校验器、
  /ai/execute 只认 add_tx/add_tx_batch、proposal_contract_version 无读取方
  （归 05b）；② 任何工具实现（05b/05c/05d）；③ 设备通道（05e/05f）；
  ④ payload 字段形状不入契约（由 op 的工具注册表条目拥有）。
修改的文档、契约与代码：
  contracts/action-proposal/schema-v2.json（新）、
  contracts/action-proposal/v1-action-aliases.json（新）、
  contracts/action-proposal/fixtures/v2/（新，17 个）、
  app/src/domain/action-proposal.ts（+v2 车道与双读入口，v1 一行未动）、
  app/src/__tests__/domain/action-proposal-v2.test.ts（新）、
  server/src/modules/ai/validator.py（+validate_proposal_v2 /
  validate_proposal_any / canonical_op）、
  server/tests/test_ai_proposal_v2_parity.py（新）、
  docs/architecture/ai-architecture.md、docs/adr/ADR-009-*、docs/APP-CONTEXT.md
验证命令与结果：
  cd app && npx tsc --noEmit                       → 0 错
  cd app && npx vitest run                         → 84 files / 1327 passed（+1 文件 +56 例）
  cd server && python -m pytest tests/ -q           → 1242 passed / 37 skipped
  ./gradlew :app:testDebugUnitTest --rerun-tasks    → BUILD SUCCESSFUL，424 tests / 0 失败
  新增用例：server/tests/test_ai_proposal_v2_parity.py 30 例；
  app/src/__tests__/domain/action-proposal-v2.test.ts 56 例
数据/同步/AI/安全影响：
  AI 协议面：新增 v2 契约与 34 个 op 的注册表（架构 §3.2 全量 CRUD 的契约形态）。
  两侧校验器新增 v2 车道，v1 车道（含 /ai/validate 的实际路径）行为逐字不变。
  **未产出任何 v2 提案**（无生产者），同步、数据模型、Kotlin 均零改动。
  安全：三条铁律在 v2 下一字未放松；审批四值与 02e 挂起—放行路径不变。
  **一处需要后续卡注意的代价**：v2 的结构校验不含 payload 级授权，
  结构通过不是执行许可——payload 授权必须在服务端组装/执行时施加。
兼容与回退方式：
  v1 的 schema.json / 两侧 v1 校验器 / 16 个 v1 fixture 全部原样，
  旧客户端行为不变由「v2 是新增文件」这一布局保证。
  回退＝不产出 v2 提案即可，无数据迁移、无契约文件替换、无服务端开关。
  协商按客户端声明（/ai/agent 的 proposal_contract_version，缺省 1）走灰度。
开放风险与责任人：
  ① 服务端接线归 05b（校验器分流 + execute 放行 + 读协商字段），
     且必须一并设计 payload 级授权——不能只换校验器；
  ② 「v1 车道产不出 v2-only op」是实现约束（旧校验器会整条判无效→卡片不出、
     写入静默失败），05b 起新增的工具必须在 v1 车道上不可用或降级为说明；
  ③ config 的 6 个子操作退场时机未定（05b / 05h）；
  ④ 客户端渲染 v2 提案的路径不在任何单卡职责内——P4 收口按覆盖度清单裁决
     （已记入 ADR-009「已知残余 3」与 **R-PROD-31**，责任人未指派）。
下一项未阻塞任务：05b（记账与账户账本域）与 05e（设备通道服务端）——批次 2，
  两卡不同文件面，可并行。
```

### 05e 设备能力通道：服务端协议与 loop 暂停/恢复（2026-09-19，`ec05120`）

```text
任务编号：T-M7-05e（P4 批次 2，服务端侧）
完成范围 / 明确未做：
  完成——① 设备能力目录（6 个能力：单向 haptic.feedback / widget.refresh /
  share.text，需结果 permissions.request / image.pick / voice.transcribe）；
  ② `device_request` 帧（八类事件里的**首个生产者**）与完整信封；
  ③ 需结果类的**进程内挂起表**（devices.DeviceChannel）+ loop 的挂起/恢复；
  ④ 上行端点 `POST /ai/device-result`（404 覆盖四种「回不去」的情形）；
  ⑤ 超时与取消的收敛（不留悬挂请求）；⑥ `AgentObserver.device_request` 扩展；
  ⑦ 开关与超时两项配置；⑧ ADR-010 + 契约文档 + 架构同步 + R-PROD-34/35 +
  D-PROD-16；⑨ 顺手改正 stream.py 把设备通道写成「归 P2」的笔误（实为 P4）。
  未做（刻意，非遗漏）——① **客户端执行器与「能力名 → 桥命令」的分派表**（05f）；
  ② 设备工具**只登记不接线**（开关默认关闭，模型看不到它们，行为与 P1 逐字相同）；
  ③ `StopReason` **不新增取值**（ADR-010 决策 4：设备失败是工具级结果）；
  ④ **不接 `device_request` 日志事件生产者**（决策 5：配对的 tool 结果已留档）；
  ⑤ 域路由与预路由（05g）；⑥ 设备工具的选择准确率实测（随 05f 走查）。
修改的文档、契约与代码：
  server/src/modules/ai/devices.py（**新**：协议 / 目录 / 挂起表）、
  agent.py（观察者钩子 + `AgentLoop(device=…)` + `_device_call` + 4 个错误码）、
  stream.py（帧载荷 + 观察者 + `_build_loop` 接线 + 步骤卡文案 + 笔误改正）、
  routes.py + schemas.py（上行端点）、config.py（`ai_device_channel_enabled` /
  `ai_device_timeout_seconds`）、conversation.py（事件表注释）、
  tests/test_ai_device_channel.py（**新**，45 例）、
  tests/test_agent_stream.py（帧级 3 例 + 既有用例的注释）、
  tests/test_ai_proposal_v2_parity.py（**顺手修**：缺文件末尾换行，非本卡范围）、
  docs/adr/ADR-010-device-capability-channel.md（**新**）、
  docs/architecture/ai-architecture.md（设备能力通道协议小节）、
  docs/architecture/ai-agent-architecture.md（§4.3 / §6.2 / §8.1 / §8.4 / §12.2）、
  docs/risk-register.md（R-PROD-34 / 35）、docs/APP-CONTEXT.md（D-PROD-16）
验证命令与结果：
  cd server && python -m pytest tests/ -q          → 1290 passed / 37 skipped
                                                    （基线 1242 + 本卡 48）
  cd server && ruff check src/ tests/              → 全绿
  cd app && npx tsc --noEmit                       → 0 错
  cd app && npx vitest run                         → 84 文件 / 1327 passed
  ./gradlew :app:testDebugUnitTest --rerun-tasks   → BUILD SUCCESSFUL，424 tests /
                                                    0 失败（1 skip）
  新增用例：test_ai_device_channel.py 45 例（协议 / 目录 / 单向 / 暂停恢复 /
  四个失败结局 / 串行配对 / 超时 / 取消 / 归属 / 端点）；帧级 3 例在
  test_agent_stream.py::TestDeviceChannel（真 SSE 链路上的挂起 → 上行 → 恢复）
数据/同步/AI/安全影响：
  AI 协议面：**八类事件与其余七类的载荷一字未改**（本卡只补生产者与回传通道，
  遵卡面职责边界）；新增一条上行端点 `POST /ai/device-result`。
  **数据面零改动**：不建表、不迁移、不写事件日志（ADR-010 决策 5）。
  安全：三条铁律不放松——设备回传的 `data` 经 `_without_ids` 剥掉内部 id 才回喂
  模型（用例钉住 attachmentId 不出现在下一次模型请求里）；上行端点按 user_id 校验
  归属，别人的 request id 与不存在的 id 一律 404（**不是 403**，不泄漏存在性）；
  设备失败只回安全枚举码，不含设备侧原文。Token 不出 Kotlin 层（ADR-003 不变）。
兼容与回退方式：
  开关 `AI_DEVICE_CHANNEL_ENABLED` **默认 false** → 与 P1 逐字相同（模型看不到
  设备能力、不产出 `device_request` 帧）。回退＝关掉开关：无数据迁移、无契约文件
  替换、无客户端改动（现有客户端本来就只把该帧原样留存、不渲染）。
开放风险与责任人：
  ① **R-PROD-34**：挂起态是进程内单例——多 worker 时回传可能落到另一个进程
     （等满超时窗才收敛）、进程重启会丢掉挂起请求（上行拿 404），两种失效形态都
     **不会错写数据**；生产若改多实例，必须先换共享存储。责任人：部署执行者 /
     M7 收口；
  ② **R-PROD-35**：05f 就位前不可端到端（开关默认关闭故不构成故障）；开启时点与
     逐能力走查（权限 / 图片 / 语音 / 分享 / 触感 / 小组件）归 05f；
  ③ 设备工具的选择准确率（R-PROD-17 口径）未实测——本卡只登记 6 个能力，实测数据
     随 05f 的端到端走查取得；
  ④ `device_request` 帧在客户端**没有渲染路径**（`use-assistant-stream` 只把它原样
     留存进 `deviceRequests`），归 05f。
并发会话记要（供下一卡参考）：
  本卡施工期间工作区同时存在另一会话的 **05b 在途改动**（`tools.py` 的契约车道
  过滤 `tool_supports_contract`），一度使 11 条既有用例变红（工具集断言与写路径）。
  **归因方式**：在干净 HEAD 的 `git worktree` 上复跑同一组用例（144 passed），再
  把本卡改动拷进去复跑（144 passed）→ 确认失败与 05b 在途改动同源、本卡零回归；
  该改动随后从工作区移除，最终全量 1290/37 与干净树一致。本卡提交与其**零交集**
  （文件面不重叠：它改 tools.py / validator.py，本卡改 devices.py / agent.py /
  stream.py / routes.py / schemas.py）。
下一项未阻塞任务：**05f**（客户端执行器与设备工具接线——直接消费本卡的帧与端点，
  是打开开关的前置）；工具全量化面为 05b（记账与账户账本域）/ 05c / 05d。
```

### 05b 全量工具：记账与账户账本域 + 服务端接线（2026-09-20，`733379e`）

```text
任务编号：T-M7-05b（P4 批次 2）
完成范围 / 明确未做：
  完成——① **记账域与账户账本域全量 CRUD**：12 个新工具（transaction_update /
  transaction_delete / category_create|update|archive / account_create|update|archive /
  book_create|update|delete / meta_set），工具集 15 → 27；② **域拆分**：P1 的
  `ledger` 域按操作面拆成 category / account / book，另立 meta 域（6 域，每域 ≤8）；
  ③ **05a 交接的服务端接线全部落地**：契约车道（注入面 + 组装面）、校验器分流、
  payload 级授权（组装 / /ai/validate / /ai/execute 三处同一函数）、execute 放行
  （op → 执行体表）、读协商字段（/ai/agent 的 proposal_contract_version）；
  ④ 写工具产出的草案统一为**规范 v2 形状**，v1 信封由组装器按车道装出（旧客户端
  行为逐字不变）；⑤ 真实调用抽样（R-PROD-17 数据点）。
  未做（刻意，非遗漏）——① 预算 / 定时 / 角色 / 分析等其余域的工具（05c / 05d）；
  ② 域路由与预路由（05g）；③ Skill 封装（05h）；④ **客户端渲染与执行 v2 提案的
  路径**（R-PROD-31，P4 收口裁决）；⑤ 设备能力通道（05e 服务端已交付，客户端执行器
  归 05f）——本卡只把 meta_set 登记为设备 op 并让它挂起。
修改的文档、契约与代码：
  server/src/modules/ai/tools.py（+12 工具 / 域拆分 / 车道过滤 / 定位条件 schema）、
  authorization.py（**新**：payload 级授权，fail-closed）、
  proposal_ops.py（**新**：op → 执行体表 + sync 登记）、
  agent.py（车道组装 + 授权 + 设备 op 直挂 + _affected_count）、
  service.py（validate_proposal 分流 + execute_proposal 的 v2 分支）、
  validator.py（别名表的反向索引 v1_actions_for_op）、
  routes.py（/ai/agent 读协商字段 + /ai/validate 的 v2 授权）、
  schemas.py（proposal_contract_version: Literal[1,2] = 1）、
  stream.py（透传契约版本 + 12 条步骤卡文案）、
  scripts/probe_tool_selection.py（**新**：真实调用抽样探针）、
  tests/：test_ai_tools_crud.py（**新** 28 例）、test_ai_proposal_v2_execution.py
  （**新** 20 例）、test_agent_stream.py::TestContractLane（**新** 5 例）、
  test_ai_tools.py / test_ai_agent.py / test_ai_agent_approval.py（P1 期断言的
  口径更新：写工具 op 集合、v1 车道注入面、草案形状——语义未放松）、
  docs/architecture/ai-architecture.md（「服务端接线」新节 + 设备 op 口径）、
  docs/architecture/ai-agent-architecture.md（§3.2 的 meta.set 执行位置**订正** + §7.2 域分组实况）、
  docs/risk-register.md（R-PROD-17 数据点 + R-PROD-31 现状）
验证命令与结果（**隔离子树 `git worktree` 内自洽**——主工作区当时有 05f 在途改动）：
  cd server && python -m pytest tests/ -q          → 1344 passed / 37 skipped
                                                    （05e 基线 1290 + 本卡 54）
  cd app && npx tsc --noEmit                       → 0 错
  cd app && npx vitest run                         → 84 文件 / 1327 passed
  ./gradlew :app:testDebugUnitTest --rerun-tasks   → BUILD SUCCESSFUL，424 tests /
                                                    0 失败（1 skip）
  真实调用抽样（server/scripts/probe_tool_selection.py，车道 v2 / 27 工具 /
  deepseek-flash / 27 用例）→ 首调命中 19/27 = 70%，两步内 25/27 = 93%，
  **零例选错写工具**（详见 risk-register R-PROD-17 与下「开放风险」）
  合流后复认（05b + 05e + 05f 同一棵树，2026-09-20）→ 服务端 1344 / 37、
  tsc 0 错、vitest 85 文件 1338 passed、JVM 439 tests / 0 失败（1 skip）；
  四板全绿。**本卡与 05e / 05f 零回归**（它们分别只动服务端设备通道与 app 侧，
  与 05b 的文件面不重叠；三卡合流时各跑一次全量）
数据/同步/AI/安全影响：
  AI 协议面：契约文件（schema.json / schema-v2.json / 别名表 / fixtures）**一字未改**
  ——本卡只接线与实现工具。**v1 车道的产出与 P1 逐字相同**：v1 客户端仍只注入 15 个
  工具，记账草案装出的信封与 02c 一致（`test_v1_lane_carries_only_the_ops_it_can_execute`
  与既有 v1 用例共同钉住）。
  数据面：**零迁移、零新表**。服务端权威直写（v2 op 执行）每次写库都在同事务登一条
  sync change——不登的话任何设备 pull 都看不到（T14-8 口径）。
  安全：三条铁律不放松。**改 / 删 / 归档类工具只接受「定位条件」**（模型永不输出
  UUID 延伸到「指向一个已有对象」这一面）；定位不唯一 → `ambiguous_transaction`，
  报可行动的错、**绝不猜**；payload 级授权覆盖分类 / 账户 / 账本 / 交易四类目标
  （交易目标额外校验**就在提案的账本里**）；**未登记授权规则的 op 一律拒绝**
  （fail-closed）；`account_delete` 这类最高危 op **不在执行面里**（注册表成员身份
  不是执行许可）。
兼容与回退方式：
  两条车道由客户端声明驱动（缺省 1）。**回退＝什么都不做**：不声明版本就永远走 v1
  车道，行为与 P1 逐字相同；无数据迁移、无契约文件替换、无服务端开关。
  **v1 车道的上限**：v1 客户端拿不到 05b 起的任何新工具——这是设计（声明字段天然
  按客户端版本灰度），不是缺陷。
开放风险与责任人：
  ① **R-PROD-31 现在站在门口**（客户端声明版本 + 渲染/执行分派两件事都没人认领）：
  v2 车道已有生产者，但客户端一侧未接 → 现阶段无人能用到 v2 提案。**不构成故障**
  （旧 APK 走 v1 车道）。责任人：P4 收口裁决；
  ② **R-PROD-17 残余收窄为「多走一步」**：真实抽样 27 例中零例选错写工具，残余 2 例
  分别表现为「拿到流水后又查了一次」与「删账本前转为文字征询」——后者在破坏性最强的
  op 上偏保守，需人工判断是否可接受（已记入 risk-register）。责任人：05g；
  ③ **回执字段语义扩张**：`AIExecuteProposalResponse.transaction_ids` 在 v2 车道上装的是
  「影响的实体 id」，字段名沿用以免动既有回执契约——等 R-PROD-31 有消费方时再谈改名。
  ④ **`meta_set` 的执行位置在设备**（工具在服务端、执行在客户端）：服务端只挂起；
  真正执行它的是 05f 的执行器。**开 05f 之前它是个「挂了但没人执行」的提案**——与
  reminder_set 同形。
下一项未阻塞任务：**05c**（预算存钱 / 定时 / 角色三域——沿用本卡的注册表与执行体范式：
  工具 → authorization 的授权规则 → proposal_ops 的执行体，三处同时表态；**三处缺一
  就是半截接线**，`test_every_server_executed_op_is_also_authorized` 会拦住它）与
  **05g**（域路由：6 域已就位，`tools_for_domains` 的 `contract_version` 参数就是
  域路由的注入入口）。
并发会话记要（供下一卡参考）：
  本卡施工期间工作区同时有 **05e**（设备通道服务端）与 **05f**（客户端执行器）在途。
  **隔离方式**：整体在 `git worktree`（`aozora-05b`，分支 `t-m7-05b`）里做，主工作区
  只留别人的改动；四板也在隔离树里跑（`app/node_modules` 用 **junction** 接过去、
  `local.properties` 与 `server/.env` 各拷一份——都是 gitignore 的本地文件）。
  **收尾顺序**：05e 提交 → 本卡 rebase 到它之上（agent.py / stream.py 有 4 处
  「两侧各加一段」的冲突，保留双方）→ 05f 提交 → 再次 rebase（零冲突）→ fast-forward
  并入 main 并推送。**教训**：计划里写的「05b 只改 tools.py、05e 改 agent/stream/routes」
  与实际不符——05a 交接给 05b 的服务端接线必然要动那四个文件；**卡面依赖表的「文件面」
  一栏在拆卡时容易想当然，领取前应以实际改动为准**。
  **另一处值得记的**：四板里 TS / JVM 两块与本卡零交集，但在共享树上会被别人的在途
  改动污染（实测：05f 在途时 bridge 命令计数用例红）——**要报告的是「本卡的四板」，
  就得在隔离树里跑**，否则会把别人的半成品记成自己的失败。
```

---

## 七、参考

- `docs/milestones/production-execution-plan.md` M7 章节 — 任务边界与验收（**权威**）
- `docs/architecture/ai-agent-architecture.md` — 目标架构（§3 功能全覆盖盘点与映射、§4.3 设备能力通道、§4.4 Loop、§7 工具系统、§10 迁移路径、§12.2 未决问题）
- `docs/architecture/ai-architecture.md` — 当前已实现契约（**05a 的主改文件**）
- `docs/APP-CONTEXT.md` §6.3 — ActionProposal 最小契约（v1 的现状）
- `contracts/action-proposal/` — schema 与 fixtures（**05a 的主改目录**）
- `docs/milestones/T-M7-02-P1-implementation-plan.md`、`T-M7-03-P2-...`、`T-M7-04-P3-...` — 上游逐卡细则与中间结果
- `docs/risk-register.md` R-PROD-15 / 17 / 22 — P4 相关风险
- `docs/engineering/需求变更规范.md` — L1/L2/L3 变更级别与文档先行要求（05a 走 L3）
- `docs/engineering/通用任务执行规范.md` — 开发实现 / Review / Bug-fix 三类工作流