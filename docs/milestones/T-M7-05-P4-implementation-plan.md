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
| **05c** | 全量工具：预算存钱、定时、角色域 | ✅ 完成 | 2026-09-20 | `20d6111` | 批次 3；16 个新工具（预算写 3 + 存钱 4 + 定时 4 + 角色 5），工具集 27 → 43 / 9 域；`persona_switch` 是设备执行 op；无契约文件改动、无迁移 |
| **05d** | 全量工具：分析与其余服务端域 | ✅ 完成 | 2026-09-20 | `aecf699` | 批次 4；**11 个新工具 / 5 个新域** → 54 个 / 14 域；新增 `HIGH_RISK_OPS`（第三个审批理由）；`consent_record` / `sync_trigger` 是设备执行 op；`account_delete` **刻意不接线**；真实抽样 05d 组 10/11（见 §六） |
| **05e** | 设备能力通道：服务端协议与 loop 暂停/恢复 | ✅ 完成 | 2026-09-19 | `ec05120` | 批次 2；服务端侧全部交付，客户端执行器归 05f；开关默认关闭（`AI_DEVICE_CHANNEL_ENABLED`） |
| **05f** | 设备能力通道：客户端执行器与设备工具接线 | ✅ 完成 | 2026-09-20 | `62199c0` | 批次 3；命令计数 83 → 85；桥侧执行器（分派 + status 归一化）+ WebView 侧编排；六能力**逐条走查通过**；闸门默认值未动（见 §六） |
| **05g** | 域路由与预路由 | ✅ 完成 | 2026-09-20 | `049b62d` | 批次 5；**规则词表**（不调模型）+ 未命中即全量（`ADR-011` / `D-PROD-17`）；`phase: routing` 生产者接通；实测：**漏注入 0/58**、注入面 **−85%**、准确率两臂无差异（见 §六） |
| **05h** | Skill 封装与按需加载 | ⏳ 未开始 | | | 批次 4 |
| **05i** | 帮助文档检索（**纳入 P4**；理财知识砍掉） | ✅ 完成 | 2026-09-20 | `a221b5e` | 批次 5；**产品帮助语料 28 条 + 确定性检索 + `help` 域读工具**（15 域 / 55 工具）；**理财知识（外部行情/产品/收益检索）按用户指令砍掉不做**；真实调用抽样 **10/10**（补问法信号前 6/10）；语料漂移风险 = **R-PROD-40** |
| **05j** | **偏好项上云 + `meta_set` 服务端执行**（用户 2026-09-20 追加目标） | ⏳ 未开始 | | | **P4 追加项**；数据面先行（参考 T-M7-01 三实体上云），再让 AI 改默认项 / 当前角色 / 用户画像；在此之前 `meta_set` 的「设备执行」是**过渡态** |
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

- **交付（2026-09-20，**纳入 P4**）：** 用户指令把范围**再收窄一层**：**只做产品帮助**，
  **理财知识（外部行情 / 产品 / 收益数据检索）直接砍掉**（`help.py` 的 docstring 与架构
  §7.2 都写明「本卡不做」）。落地＝`server/src/modules/ai/help.py`（**28 条语料** + 确定性
  关键词检索；语料**逐条按当前实现写成**——入口文案取自 App 内的真实字符串，不含未实现的
  功能、不含「即将支持」这类承诺）+ 工具侧按注册表范式接线（新域 `help`、读工具
  `help_search`、`stream.py` 的步骤卡文案）+ 域路由的 help 组信号（**问法**而非名词）。
  **读工具与契约车道无关**，两条车道都注入——**不需要客户端改动、不需要新契约版本**。
  验证：用例 50 例 + **真实调用抽样 10/10**（`scripts/probe_help_selection.py`）。
  语料随实现漂移的风险登记为 **R-PROD-40**。

### 05j 偏好项上云 + `meta_set` 服务端执行（P4 追加项）

- **为什么有这一卡（用户 2026-09-20 明确的目标）：** 05b 核对实现时发现 `meta.set` 的几个键（默认账本 / 默认账户 / 当前角色 / 用户画像 / 小组件配置）**只存在设备本地的 `sync_meta`**，桥命令 `meta.set` 的注释明写「不加实体列、**不上行**」。05b 因此把 `meta_set` 登记成**设备执行**的 op（服务端只挂起、由客户端在设备上执行）。用户的目标是：**这些项也要能由 AI 改**——若卡在「数据不上云」，那就**先按 T-M7-01 的做法把上行链路打通，再做 AI 指令**，而不是把 AI 能力永久让给设备通道。
- **内容（分两段，顺序不可倒）：**
  1. **数据面上云**：把用户级偏好项做成可同步实体（参照 T-M7-01 的三实体：扩 sync 的 `entity_type`、服务端建表、客户端写穿 + pull 合并、存量本地值**一次性上行迁移**（镜像 `migrateCloudEntitiesUp` 的标记与幂等语义）、服务端对首推幂等）。**键的取舍是设计决策**：哪些键该上云（默认项 / 当前角色 / 用户画像 / 小组件配置？列表顺序？预算沿用开关？）与哪些是**设备自管量**（`sync_cursor` / `master_book_id` / 迁移标记 / `personas_seeded` / `budget_carryover_mark` —— 这些**不该也不会上行**）必须在动工前定下来。**表示形态**（一键一实体 vs 一条 `user_preferences` + JSON）也需要一次取舍。
  2. **AI 侧接线**：`meta_set` 从 `DEVICE_EXECUTED_OPS` 移出、进 `proposal_ops.EXECUTORS`（服务端执行）+ `authorization` 的授权规则——**三处同时表态**的范式由 05b 建好，这一段的改动面很小。客户端改为**随 pull 应用**（与其它实体同路径），因此**不再需要为它单独做设备执行分派**（R-PROD-31 的渲染面因此缩小一块）。
- **职责边界：** **不改三条铁律与审批门**；**不扩 AI 能改的键**（对话可改的仍是架构 §3.2 列的那几个，`sync_cursor` 这类设备自管量永远不进工具）；**不动 05c / 05d 的工具定义**（它们拥有 key 枚举与描述，本卡只改「执行位置 + 数据面」）。
- **产出：** 服务端 sync 实体 + 迁移；`app/src/main/java/com/aozora/app/db/*`（Room 实体 / DAO / 上传迁移）；`app/src/platform/bridge/*`；`server/src/modules/ai/{tools,authorization,proposal_ops}.py` 的 op 位置调整；测试。
- **验证：** 双设备真机换机后默认项 / 当前角色 / 用户画像**跟着走**；存量本地值一次性上行不丢（旧版本客户端升级路径）；AI 改默认项 → **服务端执行** + sync 登记 + 本机 UI 随 pull 更新；离线改与云端改的收敛有明确结论；**设备自管量一个都没上行**（用例钉住白名单）。

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
| 1 | ~~**工具全集归 P4**~~——**已闭环**：05b 记账与账户账本域（15 → 27）、05c 预算存钱 / 定时 / 角色（→ 43 / 9 域）、**05d 分析与其余服务端域（→ 54 / 14 域）**。架构 §3.2 的覆盖度清单至此只剩「提醒」一行（下一条） | 02c 职责边界 | **已闭环**（05d） |
| 2 | ~~**`device_request` 无生产者**~~——**服务端侧已闭环（05e，`ec05120`）**：两类协议 + 首个生产者 + 上行端点；残留只剩客户端执行器与渲染路径 | `stream.py:34,72` | **05f** |
| 3 | ~~**`routing` 阶段无生产者**——`stream.py` 现注释「属域路由，P4 才有」~~——**已闭环（05g，`049b62d`）**：`phase: routing` 的首个生产者（第一个 Step 入口发一次，`step` 恒为 0，载荷带 `domains` / `matched`），P2 03c 的占位文案随之接通 | `stream.py:30` | **已闭环**（05g） |
| 4 | ~~**域路由归 P4**（架构 §12.2 已定第 2 项；P1 的「快路径」由 loop 自然产生，不额外路由）~~——**已闭环（05g）**：形态定为**规则词表**（不调模型、判据只看本轮输入、未命中即全量），与入口推理分级仍是两件事 | 架构 §12.2 | **已闭环**（05g） |
| 5 | **R-PROD-17（工具集过大降低选择准确率）**——`tools.py` 的域分组已就位。**05b 已交首个 27 工具的真实抽样**（首调 70% / 两步 93% / **零例选错写工具**）；**05c 把抽样扩到 47 例并连跑三次**（05c 组稳定 55% / 95%，05b 对照组 85–93%，180 次选择里 1 次错选——扩域未见可归因的下降）；**05g 收口（`049b62d`）：预路由准确率已实测——漏注入 0/58、误路由 18/58、兜底 0/58，真实调用两臂 69%/91% 对 69%/91%（逐例 52/58 相同）**。**结论要写清：域路由没有提高准确率**，R-PROD-17 的原始假设在本轮未被证实；它买到的是**每步 prompt 小 85%** 与域选择可见。**残余**：词表覆盖度（新登记 **R-PROD-39**）与「读后不落子」的形态（两臂都复现，判定与路由无关，建议随收口或 05h 处置） | `risk-register.md` | **已闭环**（05g；残余归 P4 收口） |
| 6 | **`icon.pick` / 设备类能力**属设备能力通道 | 架构 §3.2 映射表 | **05f** |
| 7 | **`ai_agent_step_timeout_seconds=20` 是初值**（02d 实测深档单步 5–7 s，真实分布待统计） | 02g / 02g-2 §五 | **收口**（全量工具与设备通道接上后按真实分布校准） |
| 8 | **会话级配额数值（50 轮 / 500k token）与合并窗参数均为初始值** | 02f / 02g-2 §五 | **收口**（按真实使用分布校准） |
| 9 | **R-PROD-22 残余：经真实 nginx 的 SSE 端到端确认** | 02g §五 | **收口 / 部署执行者**（P4 覆盖度走查时一并确认） |
| 10 | **偏好项（`meta.set` 的键）不上云 → AI 改不了默认项 / 当前角色 / 用户画像**（05b 核对实现时发现，架构 §3.2 原表把它们标成「服务端」是错的）。**用户 2026-09-20 指定目标：先按 T-M7-01 打通上云链路，再做 AI 指令** | 05b 中间结果 §六 / `BridgeProtocol.kt` 注释 | **05j**（P4 追加项；05d 只需在三处表态里改「执行位置」，key 枚举与描述仍归它）。**05c 的表态（已落地）**：当前角色**不并进 `meta_set` 的 key 枚举**，它有自己的 op `persona_switch`（同为设备执行、同样只挂起）——把「换角色」塞进 `meta_set` 会让模型在两个近义工具之间挑，而 §7.2 的经验上限讲的正是这件事；05j 收口时这两条一起移回服务端执行 |
| 10 | **`meta_set`（默认账本 / 默认账户）的客户端执行未落地**——05b 把它登记为 `tools.DEVICE_EXECUTED_OPS`（服务端只挂起、`/ai/execute` 对它回 400 `ai_device_op_not_executable`），架构文档把执行侧指派给「05f 的设备执行器」；**05f 未接**（卡面产出与验收均不含它，且它不止是"执行"一处——`ConfirmationCard` 按 `transactions` 渲染，而 `meta_set` 提案没有 transactions，卡面同样是空的）。**当前形态：用户确认后卡显示「已记录 0 笔交易」——静默无效**（比报错更糟）。见 R-PROD-37 | 05b §六 / 架构 §4.3 | **05g 已明确不认领（2026-09-20）：** 本卡的职责边界是域路由（`tools.py` / 授权 / 执行体一行未动），而这件事的真收敛路径是 **05j 的数据面**（R-PROD-37 已按用户 2026-09-20 的目标改写为「数据面先行：偏好项上云 → `meta_set` 移回服务端执行 → 客户端的执行分派与卡面渲染自然消解」）。只补「执行」或只补「卡面」都会留下另一半。**归 05j** |
| 11 | **`reminder_set` 的工具体无人认领**——05d 交付后架构 §3.2 的覆盖度清单里**只剩「提醒」一行没有工具体**。它是 v2 注册表成员、客户端三条桥命令俱在（`reminder.set` / `getStatus` / `checkPermission`），三处表态（工具 + `DEVICE_EXECUTED_OPS` + 授权规则）的成本与 `meta_set` 同形。**两张可能认领它的卡都不含它**：05f 的产出与验收列的是设备能力通道的 6 个 capability（提醒不在其中——05e 立的口径是「提醒是写工具、要过审批门，不属于设备指令通道」），05d 的卡面列的是分析 / 导出反馈隐私 / 安全账号 / 同步。05d **未越界代做**（职责边界明写「不做设备类」，越界会让「哪张卡负责什么」失去唯一答案）。见 R-PROD-38 | 05d 收尾盘点 / 架构 §3.2 | **P4 收口**（补一张极小的尾卡：三处表态 + 一条守护用例；或显式记为「P4 不做」并同步改架构 §3.2，范式见 05i 的处理方式） |
| 12 | **05d 的「部分覆盖」两行**——`meta.set` 的「小组件配置」与「用户画像」两个键仍在设备本地（`meta_set` 的 key 枚举当前只有 `default_book` / `default_account`）。**这不是遗漏**：键该不该上云、以什么形态上云是 05j 的数据面设计决策（用户 2026-09-20 指定的目标），05d 按 `meta_set` 现有的 key 集合做事 | 05d 收尾盘点 / 架构 §3.2 | **05j**（与挂账项 10 同一批收敛） |

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

### 05f 设备能力通道：客户端执行器与设备工具接线（2026-09-20，`62199c0`）

```text
任务编号：T-M7-05f（P4 批次 3，客户端侧）
完成范围 / 明确未做：
  完成——① 桥命令 device.execute / device.result（**命令计数 83 → 85**，四处清单 +
  SecurityConfigTest 断言同一次改完）；② 桥侧执行器：能力名 → 既有设备类 handler 的
  分派 + 各错误码族 → 协议四值的归一化；③ AiGatewayClient.submitDeviceResult
  （POST /ai/device-result，404 等价于成功）；④ TS 侧契约（命令常量、帧与结局类型）
  与两个桥方法；⑤ device-executor.ts（WebView 侧编排：帧 → 执行 → 需结果类顺带回传）；
  ⑥ use-assistant-stream 的 onDeviceRequest 实时转发（与 onConfirm 同范式）；
  ⑦ use-assistant / AssistantPage 接线，含语音的界面承接；⑧ 15 个 Kotlin 用例 +
  11 个 TS 用例。
  未做（刻意，非遗漏）——① 服务端一行未动（05e 已闭环，本卡是它的消费侧）；
  ② **闸门默认值不动**（ai_device_channel_enabled 仍 false，开启时点归 P4 收口）；
  ③ **不自动开麦**——模型下发 voice.transcribe 时替用户打开麦克风是不该有的默认
  （T12-1 的语音按需授权口径），执行器只置位、由用户点既有 mic 按钮；
  ④ 不回传失败重试（服务端的挂起有 ai_device_timeout_seconds 自己的收敛路径）；
  ⑤ R-PROD-17 的选择准确率实测（1 个数据点，见下）；
  ⑥ **`meta_set` 的客户端执行未接**——05b 把它登记为 `tools.DEVICE_EXECUTED_OPS` 并在
  架构文档里把执行侧指派给「05f 的设备执行器」。**本卡刻意不接**：卡面的产出与验收
  列的都是**设备能力通道**（`device_request` 的 6 个 capability），而 `meta_set` 是
  ActionProposal 的一条 op——它要的是**确认卡链路的改造**（执行时按 op 分派到设备、
  卡面按 op 渲染），不是设备通道的分派表。只补"执行"会得到一个仍然空白的卡面。
  已登记为挂账项 10 + R-PROD-37（现状是**静默无效**，见下）。
修改的文档、契约与代码：
  app/src/main/.../bridge/BridgeProtocol.kt（+2 常量 + ALL）、AozoraBridge.kt
  （dispatch +2 分支、handleDeviceExecute / handleDeviceResult / deviceOutcome /
  requestDevicePermission）、ai/AiGatewayClient.kt（submitDeviceResult）、
  app/src/platform/bridge/bridge-types.ts（命令 + DeviceRequestFrame /
  DeviceExecuteResult / DeviceOutcomeStatus / DeviceResultAck）、native-bridge.ts
  （executeDevice / resultDevice）、features/assistant/device-executor.ts（新）、
  use-assistant-stream.ts（onDeviceRequest + 投影注释笔误改正）、use-assistant.ts、
  AssistantPage.tsx、SecurityConfigTest.kt（83 → 85）、
  app/src/test/.../bridge/AozoraBridgeDeviceTest.kt（新，15 例）、
  app/src/__tests__/features/device-executor.test.ts（新，11 例）、
  native-bridge.test.ts（命令清单 + 计数 85）。
验证命令与结果（**合流后复跑**——05b 的 `733379e` 已落在本卡之后，见「并发记要」）：
  cd app && npx tsc --noEmit                     → 0 错
  cd app && npx vitest run                       → 85 files / 1338 passed（+1 文件 +11 例）
  ./gradlew :app:testDebugUnitTest --rerun-tasks  → BUILD SUCCESSFUL，439 tests / 0 失败
                                                    （+15：AozoraBridgeDeviceTest）
  cd server && python -m pytest tests/ -q          → **1344 passed / 37 skipped**
                                                    （合流后；本卡零改动，1290 → 1344 的增量
                                                    全部来自 05b 的服务端用例）
  **逐能力走查（模拟器 Pixel_7_API_36，真实 Android 路径）**：
  走查前置（重要，后续走查者必读）：服务端需 AI_DEVICE_CHANNEL_ENABLED=true **且**
  AI_AGENT_ENABLED=true（后者默认 false，端点直接 503 agent_disabled）；客户端
  **AI_AGENT_CLIENT_ENABLED 默认 false**，走的是 ai.interpret 老链路（无工具集、
  无 device_request 帧）——走查期间临时置 true 并重建 APK，**走查后已还原**
  （git diff 为空，见「兼容与回退」）。
  ① 桥直调（device.execute × 7）：触感 → {status:ok,data:{ok:true}}；小组件 →
  {status:ok,data:{success:true}}；未知能力 → unavailable；缺 capability →
  invalid_input；空文本分享 → failed；权限已授予 → {status:ok,data:{permission,
  status:"granted"}}；相册取消 → **denied**（真实 PhotopickerGetContentActivity
  弹出后 BACK）。**系统权限框与相册选择器都真实弹出**，证明分派落到了既有命令上。
  ② 端到端·单向（「给我一个震动反馈」）：phase(thinking,fast) → tool_call
  device_haptic「正在给一次触感反馈…」→ device_request{kind:one_way} →
  tool_result **status:ok** → phase(thinking,deep) → delta→ **done completed**
  （steps 2 / tool_calls 1）——**下发即继续、不等回传**成立。
  ③ 端到端·需结果（「帮我从相册里选一张图片」）：device_request{kind:result,
  capability:image.pick,timeout_seconds:120} → 相册弹出 → 用户取消 →
  tool_result **code:device_denied**（服务端 ERR_DEVICE_DENIED 文案）→
  **loop 继续**（step 2 deep）→ 模型按降级文案解释「这次相册选择被取消了……
  需要我改成直接拍照，跟我说一声就行」→ done completed。
  ④ 端到端·语音（「请让我说一段话，你转成文字」）：device_request
  {capability:voice.transcribe} → **页面出现提示条**「🎤 助手想听你说一句——点麦克风
  开始」+ 步骤卡「正在等待语音输入…」 → 点 mic（先真实弹出 RECORD_AUDIO 权限框，
  授予后）→ 录制（mic 变「停止录音」）→ 停止 → 空文本按 denied 结算 →
  tool_result device_denied → **loop 继续** → 模型解释并提示可去设置页开权限 →
  done completed。
数据/同步/AI/安全影响：
  AI 协议面：device_request 从「有生产者」变成「有消费者」——两处协议取值
  （DELIVERABLE_STATUSES 四值、kind 两值）在 Kotlin 侧有了同构副本。
  同步 / 数据模型：零改动（设备动作不写库）。
  安全：**Token 不出 Kotlin 层**（ADR-003 不变）——回传 POST 也走 AiGatewayClient；
  三条铁律未触及；设备动作不占写预算（05e 口径）。
  分派表只调用既有 handler、不复制它们的实现（既有的界面直调路径一行未改）。
兼容与回退方式：
  旧 APK（命令表无 device.execute/device.result）→ device-executor 的能力探测
  直接安静退出（帧原样留在投影里，服务端等满超时自行收敛），界面不报错。
  走查期间的 AI_AGENT_CLIENT_ENABLED 临时置 true **已还原**，工作区对 HEAD 无差异。
  整卡回退＝不打开 ai_device_channel_enabled。
开放风险与责任人：
  ① **R-PROD-35 更新**（05f 就位、六能力走查通过；剩「开启时点」归 P4 收口）；
  ② 新增 **R-PROD-36**：设备工具的**选择准确率只有 1 个数据点**（本卡 4 条走查输入
     里 4 次命中、0 次误选），量级不足以支撑 R-PROD-17 的结论——按 R-PROD-17 口径
     的实测归 P4 收口（责任人：05g / 收口）；
  ③ 语音的录音内容在模拟器上为空（无声输入），**真机口述的转写回传路径未走**
     （空文本 → denied 那条已走）——归真机走查（责任人：收口）。
  ④ 新增 **R-PROD-37**：`meta_set` 提案在客户端是**静默无效**的（确认后卡显示
     「已记录 0 笔交易」，实际什么都没写）。这是 05b 的 `DEVICE_EXECUTED_OPS` 与
     客户端确认卡链路之间**没有对接**的结果——本卡未接的理由见「未做 ⑥」，
     归属见 §五 挂账项 10。
并发记要（供下一卡参考）：
  ① 本卡提交（62199c0）之后，05b（733379e）落在同一个 main 上——**它改的是服务端**，
  与本卡的 app/ 文件面零交集；本卡因此在合流后复跑了四板，数字如上。
  ② **本轮走查的最大时间陷阱是客户端 agent 流的默认关闭**：不打开它，输入会走
  ai.interpret 老链路（本地 NLU 认得记账句就直接本地写入），表现为「模型回复了但
  一个流帧都没有」——先查开关再怀疑链路。
  ③ 模拟器小坑：用 KEYCODE_BACK 关掉系统权限框会把 App 一并退出（WebView 调试端口
  随之失效，需按新 PID 重建 adb forward）；相册选择器同理会退出。**授予权限请用
  `adb shell pm grant`，不要点框上的按钮或按 BACK**。
远端状态（2026-09-20）：
  已推送 `origin/main`（`c31d3a1` → `aa97083`）。**CI 未能确认**：push 触发的
  CI / Secret Scan 作业**全部在 4 s 内 failure**，注解为「The job was not started
  because recent account payments have failed or your spending limit needs to be
  increased」——**属账户计费层问题，不是本次改动**（与 T-M7-04 收口时记录的同一
  情形，05b 的推送同样如此）。四板结论以本地实测为准（数字见上）。
  另记一笔网络：首次 push 遇 `github.com:443` 连接重置（`curl` 返回 **000**，
  而 `api.github.com` 同时是 200——与 R-PROD 之外的既有经验一致：**判据要用
  `github.com` 而不是 api 域名**），重试后恢复，`git -c http.version=HTTP/1.1 push` 成功。
下一项未阻塞任务：05c（预算存钱 / 定时 / 角色域）与 05h（Skill 封装）——批次 3/4，
  两卡文件面不同；05d 依赖 05c 的注册表形态。
```

### 05c 全量工具：预算存钱、定时、角色域（2026-09-20，`20d6111`）

```text
任务编号：T-M7-05c（P4 批次 3）
完成范围 / 明确未做：
  完成——① **三域全量读写**：16 个新工具（`budget_set` / `budget_set_category` /
  `budget_delete`、`savings_create|update|archive|list`、
  `scheduled_create|update|archive|list`、
  `persona_create|update|archive|switch|list`），工具集 27 → 43 / **9 域**；
  ② **预算的写并进已有的 budget 域**（5 读 + 3 写 = 8，正好到 §7.2 的上限）；
  ③ **v2 执行面接线**（authorization 的授权规则 + proposal_ops 的执行体，与服务端
  工具三处同时表态）；④ **离线语义有明确结论**（写入即云端权威 + 同事务登 sync
  change，设备上线 pull 到——写在 proposal_ops.py 文件头）；⑤ 真实调用抽样扩到
  47 例并连跑三次（R-PROD-17 数据点）。
  未做（刻意，非遗漏）——① 分析 / 导出 / 反馈 / 隐私 / 安全账号 / 同步各域（05d）；
  ② 域路由（05g）；③ Skill 封装（05h）；④ **客户端渲染 v2 提案的路径**（R-PROD-31，
  P4 收口裁决）——本卡的 12 个写 op 与 05b 的 13 个同命：服务端产得出、客户端出不了卡；
  ⑤ **`persona_switch` 的客户端执行**（与 `meta_set` 同类的设备执行 op，归 05j 的
  数据面收敛路径，见 R-PROD-37）。
修改的文档、契约与代码：
  server/src/modules/ai/tools.py（+16 工具 / 3 个新域 / ToolContext 三套名字索引 /
  7 个新错误码）、authorization.py（3 类新目标 + 12 条 op 规则）、
  proposal_ops.py（12 个执行体 + payload 取字段小工具 + 离线语义注释）、
  stream.py（16 条步骤卡文案）、scripts/probe_tool_selection.py（CASES_05C + 分组
  记账 + 三个新读工具的假结果）、tests/test_ai_tools_05c.py（**新** 44 例）、
  tests/test_ai_proposal_v2_05c.py（**新** 32 例）、tests/test_ai_tools.py 与
  tests/test_ai_proposal_v2_execution.py（两处**口径更新**，见下）、
  docs/architecture/ai-architecture.md（服务端接线的 05c 续接段）、
  docs/architecture/ai-agent-architecture.md（§3.2 三行的数据位置实况订正 + §7.2 域表 9 域 / 43 工具）。
契约文件**一字未改**（schema.json / schema-v2.json / 别名表 / fixtures 全部不动）——
05c 的 op 都已在 05a 的 34 个成员里，本卡只是让它们「执行得了」。
验证命令与结果：
  cd server && python -m pytest tests/ -q          → **1420 passed / 37 skipped**
                                                    （05b 基线 1344 + 76）
  cd app && npx tsc --noEmit                       → 0 错
  cd app && npx vitest run                         → 85 files / 1338 passed
  ./gradlew :app:testDebugUnitTest --rerun-tasks   → BUILD SUCCESSFUL
  **app/ 零改动**（本卡纯服务端 + 文档），app 三板与 05f 收口时逐字相同。
  真实调用抽样（server/scripts/probe_tool_selection.py，车道 v2 / **43 工具** /
  deepseek-flash / 47 例 = 05b 对照组 27 + 05c 新增 20，**同一份用例连跑三次**）：
    05c 组：首调 11/20 = 55%、两步内 19/20 = 95%（三次完全一致）
    05b 组：首调 74% / 67% / 74%，两步内 **85% / 89% / 93%**（自身波动区间内）
    合计 180 次工具选择里，落到**错误**工具上只有 1 次（`category_update`，想建分类
    却去改）——**扩域未观察到可归因的准确率下降**（详见 risk-register R-PROD-17）
数据/同步/AI/安全影响：
  AI 协议面：**零契约改动、零迁移、零新表**。三条铁律不放松：三实体改 / 归档类工具
  只按**名字**定位（读工具不回传 id）；金额与统计仍由确定性代码算——**存钱的「存入」
  是服务端读当前值后累加**（`addAmountCents`），模型不读旧值、不做加法，于是既没有
  「模型算错」也没有「两人同时存、后写的覆盖先写的」；写库仍必经审批门。
  授权面：`savings_goal` / `chat_persona` 按用户级（owner_id）、`scheduled_rule`
  按账本级（额外要求 book_id 对得上，与 transaction 同口径）；**未登记规则的 op
  一律拒绝**（fail-closed）不变。
  数据面：**每次权威直写都在同事务登一条 sync change**（T14-8 口径），用例实测
  `insert/update/delete` 三种操作与版本号，并用 `/sync/pull` 验证设备拿得到
  （离线语义的正面证据）。
兼容与回退方式：
  **回退＝什么都不做**：无数据迁移、无契约替换、无服务端开关。v1 车道（旧 APK）本次
  只多看到 3 个**读**工具（savings_list / scheduled_list / persona_list）——读工具与
  车道无关是 05b 起的既定口径（它们的结果只回喂模型、不进客户端校验器），写工具在
  v1 车道上仍只有 `transaction_add` 一个。`test_v1_lane_carries_only_the_ops_it_can_execute`
  的断言从「= P1 的 15 个」改成**结构性判据**（全部读工具 + V1_LANE_OPS 的写工具），
  因为 P1 期那个数字是「14 读 + 1 写」的巧合，而判据本身没变。
开放风险与责任人：
  ① **R-PROD-31 的清单又长了 12 项**（本卡全部写 op 都要等客户端渲染面）：P4 收口
  应按**整张清单**（05b + 05c + 05d）评估成本，而不是按单卡看待。责任人：P4 收口；
  ② **R-PROD-37 追加**：`persona_switch` 与 `meta_set` 同类（设备本地键 + 客户端没有
  执行分派 = 确认后卡显示 0 笔的静默无效）；**另有一条读侧缺口**——服务端看不见
  「当前角色是哪一个」，`persona_list` 只能如实说不知道（工具描述已写明不要猜）。两条
  都随 05j 的数据面收敛一起关掉。责任人：05j / P4 收口；
  ③ **R-PROD-17 残余收窄为「读后不落子」**：三次抽样里稳定复现的 3 例（`category_create`
  / `book_delete` / `persona_update`）形态完全一致——先调一个读工具，拿到数据后**转为
  文字回答而不是把写工具调下去**；这不是选错工具（180 次选择里只错 1 次）。若域路由
  （05g）收窄注入面后仍复现，值得在系统提示里加一条「查完要接着做」的执行纪律。
  责任人：05g；
  ④ **budget 域到 8/8 上限**：05d 或后续的预算类工具不能再并进来，只能新开域
  （已写进架构 §7.2）。
并发会话记要（供下一卡参考）：
  本卡施工期间主工作区**只有本卡的改动**（05b / 05e / 05f 均已提交并推送），因此四板
  直接在主工作区跑，未开 `git worktree`。开工时 HEAD 是 `fc0dd8c`（05b 的 05j 登记
  文档提交），工作区干净。
  **一处值得记的实测**：改动中途全量套件出现 1 条红灯
  （`test_agent_stream.py::test_every_registered_tool_has_progress_text`）——**这是
  设计好的护栏**：`stream.py` 的步骤卡文案表要求覆盖注册表里每一个工具，加 16 个工具
  就必须加 16 条文案。**这类「注册表增长时的连带面」在本卡一共有四处**，领取同类卡时
  一次找齐：① 步骤卡文案（`stream.py`）；② 域数 / 工具数断言（`test_ai_tools.py`）；
  ③ 写 op 集合断言（同上 + `test_ai_proposal_v2_execution.py` 里「未接线 op」的例子
  ——它原来用 `budget_set` 举例，本卡落地后那个例子失效，换成 05d 的 `export_create`）；
  ④ `SERVER_EXECUTED_OPS` 与授权表的**双向**覆盖（`test_every_server_executed_op_is_also_authorized`
  会拦住任何半截接线）。
下一项未阻塞任务：**05d**（分析与其余服务端域）与 **05h**（Skill 封装）——批次 4，
  两卡文件面不同（05d 扩 tools.py / authorization.py / proposal_ops.py，05h 在
  prompts.py + 新 skill 模块）；05d 的授权面要特别当心**删号类工具**（最高危，必须
  最严审批且不得绕过既有删号流程）。**05g**（域路由）也已可开工：9 个域就位，
  `tools_for_domains` 的 `contract_version` 参数就是注入入口。
```

### 05d 全量工具：分析与其余服务端域（2026-09-20，`aecf699`）

```text
任务编号：T-M7-05d（P4 批次 4）
完成范围 / 明确未做：
  完成——① **五域 11 个新工具**（`analysis` 2 / `export` 2 / `privacy` 3 /
  `security` 2 / `sync` 2），工具集 43 → 54、域 9 → 14；② **v2 执行面接线**：
  4 个服务端执行体（导出 / 反馈 / 昵称 / 注销）+ 6 条授权规则（**三处同时表态**，
  与服务端工具同一个范式）；③ **新增第三个审批理由**（`tools.HIGH_RISK_OPS` →
  `confirm_reason` 的 `high_risk` 分支）——架构 §7.4 那张表的「高风险」一类此前
  没有信号源，注销是它的第一个；④ **设备执行 op 再进两条**（`consent_record` /
  `sync_trigger`）；⑤ **真实调用抽样**（R-PROD-17 数据点，58 例）。
  未做（刻意，非遗漏）——① 域路由与预路由（05g）；② Skill 封装（05h）；
  ③ **客户端渲染 v2 提案的路径**（R-PROD-31，P4 收口裁决）——本卡的 6 个写 op
  与 05b / 05c 的 25 个同命：服务端产得出、客户端出不了卡；
  ④ **`reminder_set` 的工具体**（**本卡最大的范围决定**：它是 v2 注册表成员、
  桥命令俱在，三处表态的成本也很低，但「判给谁」是一张新卡的边界问题，而本卡
  职责边界明写「不做设备类」——越界代做会让「哪张卡负责什么」失去唯一答案。
  已登记 **R-PROD-38** + §五 挂账项 12，交 P4 收口裁决）；
  ⑤ **`meta_set` 的「小组件配置」/「用户画像」两个键**（卡面未列，且它们的取舍
  是 05j 的数据面设计决策——哪些键上云，不是遗漏。见 §五 挂账项 10）；
  ⑥ **`account_delete` 不接线**（见下「一张刻意的空位」）。
修改的文档、契约与代码：
  server/src/modules/ai/tools.py（+11 工具 / 5 个新域 / `HIGH_RISK_OPS` /
  `DEVICE_EXECUTED_OPS` +2 / `_enum_text` 修 `(str, Enum)` 的 `__str__` 坑 /
  1 个新错误码 `invalid_period_range`）、authorization.py（6 条 op 规则）、
  proposal_ops.py（4 个执行体 + `_load_user`）、agent.py（`confirm_reason` 加 op
  维度）、stream.py（11 条步骤卡文案）、consent/service.py（+
  `get_latest_user_consent`）、scripts/probe_tool_selection.py（CASES_05D 11 例 +
  5 个假读结果 + 三组分别记账）、tests/：test_ai_tools_05d.py（**新** 32 例）、
  test_ai_proposal_v2_05d.py（**新** 15 例）、test_ai_agent_approval.py
  （`TestHighRiskApproval` 3 例 + `_FakeDb.scalar` 桩 + `_loop` 加
  `contract_version`）、test_ai_tools.py（`_WRITE_OPS_05B_05C` →
  `_WRITE_OPS_05B_05C_05D`，+6 个 op）、test_ai_proposal_v2_execution.py
  （两处「未接线 op」的例子从 `export_create` / `sync_trigger` 换成
  `reminder_set`——那两条本卡落地后都能执行了）、
  docs/architecture/ai-architecture.md（05d 收尾段 + op 注册表订正 +
  「未覆盖的最后一行」）、docs/architecture/ai-agent-architecture.md（§3.2
  订正二 + §7.2 14 域 / 54 工具 + §7.4 高风险一行）、docs/risk-register.md
  （R-PROD-17 数据点 / R-PROD-31 追加 / R-PROD-37 追加 / **R-PROD-38 新增**）
  契约文件**一字未改**（7 个 op 都已在 05a 的 34 个成员里）。
验证命令与结果：
  cd server && python -m pytest tests/ -q          → **1470 passed / 37 skipped**
                                                    （05c 基线 1420 + 本卡 50）
  cd server && ruff check src/ tests/ scripts/     → 全绿
  cd app && npx tsc --noEmit                       → 0 错
  cd app && npx vitest run                         → 85 文件 / 1338 passed
  ./gradlew :app:testDebugUnitTest --rerun-tasks   → BUILD SUCCESSFUL
  **app/ 零改动**（本卡纯服务端 + 文档），app 三板与 05c 收口时逐字相同。
  真实调用抽样（54 工具 / deepseek-flash / 58 例 = 05b 组 27 + 05c 组 20 +
  05d 组 11）：
    05d 组：首调 **10/11 = 91%**、两步内 10/11 = 91%  ← 本轮最高
    05c 组：50% / 90%；05b 组：78% / 89%；全部：71% / 90%
  详见 risk-register R-PROD-17（含「域越小首调越准」的解读与注销那条的形态）
数据/同步/AI/安全影响：
  AI 协议面：**零契约改动、零迁移、零新表**。三条铁律不放松：分析工具的数量
  全部来自确定性代码（`FactPack` / `StatisticsService`，模型不做算术）；读工具的
  结果**不含任何 id**（本卡新增 3 处用例钉住，含递归扫描）；写库仍必经审批门。
  **注销是唯一的高危 op**：强制确认（`HIGH_RISK_OPS`）+ 服务端内部走完既有的
  「申请 → 确认」两阶段（**一行删号逻辑都没有重写**）→ 账号只到 FROZEN，
  7 天后的物理删除仍归 `infra/cleanup/account-deletion.py`，撤销入口原样可用
  （用例实测 204 且状态回 ACTIVE）。`account_delete` **两道门都拒**（授权表未登记
  → 403；执行面无执行体 → 400）——它的 payload 要一次性 token，凭据不进模型
  上下文。
  授权面：`export_create` 按账本授权（别人的账本 → 403，跨账本 → 404）；
  其余五条空元组是**明确表态**（授权面不在 payload 里）。fail-closed 不变。
  数据面：**05d 的 op 一条 sync change 都不写**——它们动的不是可同步实体
  （`sync/schemas.py` 的八类白名单之外），登了只会让设备 pull 到一个它不认识的
  分派键。用例前后计数钉住。
兼容与回退方式：
  **回退＝什么都不做**：无数据迁移、无契约替换、无服务端开关。v1 车道（旧 APK）
  本次多看到 5 个**读**工具（分析 2 / 导出列表 / 同意状态 / 同步状态）——读工具与
  车道无关是 05b 起的既定口径；写工具在 v1 车道上仍只有 `transaction_add`。
  设备执行 op（`consent_record` / `sync_trigger`）**不构成新的敞口**：客户端没有
  op 分派表时它们只是「挂了没人执行」（与 `meta_set` 同形态，见 R-PROD-37）。
开放风险与责任人：
  ① **R-PROD-31 的清单又长了 6 项，且形态比前两轮更重**——注销那条尤其不能含糊：
  **确认卡是它唯一的用户可见面**，没有渲染路径时缺席的不只是一个功能，而是
  安全机制本身。责任人：P4 收口；
  ② **R-PROD-38（新增）**：**提醒行没有工具体**（`reminder_set` 无人认领），
  §3.2 覆盖度清单到此只剩这一行的反例。cost 很低但归属是边界问题，交收口裁决
  （补尾卡或显式记为「不做」）。责任人：P4 收口；
  ③ **R-PROD-37 再进两条**（`consent_record` / `sync_trigger`）：它们**不会上云**
  （同意与同步天生是设备行为），所以 05j 的数据面收敛关不掉它们——只能靠客户端
  补齐「渲染 + 分派」。责任人：P4 收口 / 认领者；
  ④ **R-PROD-17 残余收窄为「首调不落子」**：05d 组 11 例里唯一未中的是注销
  （模型一个工具都没调、转为文字回答）。这个保守**误差方向是安全的**（多问一次，
  不是误删账号），但代价是「用户明确要求注销时 AI 不出卡」。是否在系统提示里
  补一条执行纪律，随 05g 裁决。责任人：05g；
  ⑤ **05d 的域首调最准（91%）这一数据点值得 05g 用**：五个新域各 2–3 个工具，
  而 05c 组（每域 4–8 个）首调 50%。**域越小越准**与 §7.2 的判断方向一致——
  域路由的粒度选择可以此为依据。责任人：05g。
并发会话记要（供下一卡参考）：
  本卡施工期间主工作区**只有本卡的改动**（工作区干净、HEAD 是 `d99033c`），
  四板直接在主工作区跑，未开 `git worktree`。
  **注册表增长时的连带面在本卡是五处**（05c 记的是四处，本卡多出的一处值得记）：
  ① 步骤卡文案（`stream.py`，`test_every_registered_tool_has_progress_text` 拦住）；
  ② 域数 / 工具数断言；③ 写 op 集合断言（`_WRITE_OPS_*`）；
  ④ `SERVER_EXECUTED_OPS` 与授权表的双向覆盖；
  ⑤ **「未接线 op」的例子**——`test_ai_proposal_v2_execution.py` 里用
  `export_create` / `sync_trigger` 举例的两条用例，本卡落地后那两个 op 就能执行
  了，例子必须换成仍然未接线的（换成 `reminder_set`）。**这一处每卡都要重挑一次**
  （05c 记过同一件事：它当时把 `budget_set` 换成 `export_create`），因为
  「未接线的注册表成员」是**会耗尽**的资源——05d 之后只剩 `account_delete`（刻意）
  与 `reminder_set`（无人认领）两个。
  **一处本卡特有的开工前置**：`tools.py` 里新增的读工具要用到 `(str, Enum)`
  混入类的**取值**，而 `str(member)` 给的是 `"ExportFormat.CSV"`——这类值直接
  回喂给模型就是噪音（`_enum_text` 修掉）。**同类的值还有 `UserStatus`**，
  已一并走同一个 helper。
下一项未阻塞任务：**05h**（Skill 封装——批次 4 的另一半，文件面在 prompts.py +
新 skill 模块，与本卡零交集）与 **05g**（域路由——14 个域就位，
`tools_for_domains` 的 `contract_version` 参数是注入入口；本卡交付的
「域越小首调越准」是它的输入）。**收口**还欠一条裁决：R-PROD-38（提醒行的工具体）
与 R-PROD-31（28 个写 op 的客户端渲染面）。
```

---

### 05g 域路由与预路由（2026-09-20，`049b62d`）

```text
任务编号：T-M7-05g（P4 批次 5）
完成范围 / 明确未做：
  完成——① **域路由的形态决策与实现**：规则词表（`routing.py` 新模块）+ 未命中即
  全量（误差方向「宁可多注入」）+ 跨域共享信号（近义工具分属两域时必须都给）；
  ② **loop 接线**：注入面唯一入口 `_inject`（工具清单 / 可执行集合 / provider 载荷
  三者同源）、`run()` 里与 `classify_reasoning` 并排各算各的、显式 `tools=` 跳过
  路由；③ `AgentObserver.routed`（第六个钩子）+ `stream.py` 的 `phase: routing`
  **首个生产者**（`step` 恒为 0，载荷 `domains` / `matched`）—— 03c 的占位接通；
  ④ `ADR-011` + `D-PROD-17` + 架构 §7.2 / §8.1 / §12.2 同步；
  ⑤ 探针两档新口径：`--offline`（零模型调用的漏注入 / 误路由 / 兜底）与
  `--routing`（路由臂，与全量对照组可在同一会话里对跑）；⑥ 25 例新用例 + 2 条帧级
  用例。
  未做（刻意，非遗漏）——① **不给域路由加开关**（ADR-011 决策 8：兜底路径就是 P1
  的注入面，最坏情况是「没优化」而不是「退化」；与设备通道默认关闭的取向相反，
  理由也相反——那个是客户端执行器不存在）；② **不接「读后不落子」的提示纪律**
  （两臂都复现 → 与路由无关；它是保守型误差；改提示主体是对所有轮次的全局行为
  改动，更接近 05h 的面）；③ **不认领挂账项 10**（`meta_set` 的客户端执行——它的
  真收敛路径是 05j 的数据面，见 §五 该行的理由）；④ 不改工具定义（05b–05d）、
  不改入口推理分级（02d）、不动客户端一行。
修改的文档、契约与代码：
  server/src/modules/ai/routing.py（**新**：词表 + `route_domains` +
  `unrouteable_domains`）、agent.py（`_inject` / `_default_tools` / `run()` 的决策 /
  第一个 Step 的通知 / `AgentObserver.routed`）、stream.py（`QueueObserver.routed` /
  `ROUTING_STEP` / 两张协议表与 `Phase.ROUTING` 的说明）、
  scripts/probe_tool_selection.py（`--offline` / `--routing` / `_all_cases` 收掉三处
  重复的分组逻辑）、tests/test_ai_routing_05g.py（**新** 25 例）、
  tests/test_agent_stream.py（帧级 2 例 + 7 处帧序断言的更新）、
  tests/test_ai_agent.py（2 条边界用例改用跨域输入——它们量的是硬边界计数，不是路由）、
  docs/adr/ADR-011-domain-routing.md（**新**）、
  docs/architecture/ai-agent-architecture.md（§7.2 域路由落地段 / §8.1 的 phase 行 /
  §12.2 已定第 2 项闭环）、docs/APP-CONTEXT.md（D-PROD-17）、
  docs/risk-register.md（R-PROD-17 改写 + R-PROD-39 新增）
  **契约文件一字未改**（schema / fixtures / 两侧校验器都不动——域路由不碰提案）。
  **app/ 零改动**：唯一的客户端面是 03c 早已就位的 routing 常驻占位。
验证命令与结果（**隔离树 `git worktree` 内自洽**——主工作区当时有 05i 的在途改动，
  见「并发记要」）：
  cd server && python -m pytest tests/ -q          → **1547 passed / 37 skipped**
                                                    （基线 1520 = `a221b5e`（05i 落地后）
                                                    实测，本卡 +27）
  cd server && python -m ruff check src/ tests/ scripts/ → 本卡文件全绿
                                                    （**HEAD 上另有 3 处 05i 带入的
                                                    ruff 告警**，见「并发记要」③）
  cd server && python scripts/probe_tool_selection.py --offline → 见下「实测」
  cd app 三板：**未重跑**（本卡 app/ 零改动，与 05c / 05d 同一处理）
实测（R-PROD-17 的下一步，两条口径）：
  **离线**（58 例，零模型调用）：漏注入 **0/58**、误路由 18/58（绝大多数是刻意的
  跨域共享，无一条是「错域」）、兜底 0/58、平均注入 **1.4 域 / 6.8 工具**；
  注入面字符数 **32,418 → 平均 4,982（−85%）**，而这份载荷**每个 Step 都要重发**。
  **真实调用**（同会话两臂对跑，模型 deepseek-flash）：
    路由臂   首调 40/58 = 69%、两步内 53/58 = 91%
    对照组   首调 40/58 = 69%、两步内 53/58 = 91%     ← **完全一致**
    逐例比对：**52/58 的实际工具序列逐字相同**；8 例不同且方向两边各有。
  **结论：域路由没有提高准确率**——R-PROD-17 的原始假设（工具集过大降低选择准确率）
  在本轮**未被证实**；它买到的是**每步 prompt 小 85%** 与**域选择在步骤卡上可见**。
  顺带**订正 05d 那条读数**：「域越小越准」的成因更像是「域内近义面天然窄」，
  而不是「注入面小」（两臂在 05d 组分别是 9/11 与 10/11，噪声内）。
反向验证（守则 §3.2：把新用例打回缺陷形态，确认它真的会红，再还原）：
  6 类变异逐条跑过，全部命中（在隔离树里做，跑完 `git status` 为空）：
    (a) 关掉域路由（`run()` 里固定成全量 = 文档写的回退方式） → 红（5 failed）
    (b) `ROUTING_STEP` 改成 1                                         → 红（2 failed）
    (c) 某个域**一个信号都没有**（新增域忘补词的真实形态）           → 红（2 failed）
    (c2) 词表里域名打错字（`budget` → `budgte`）                      → 红（5 failed）
    (d) 不发 `routed` 通知                                            → 红（8 failed）
    (e) `_inject` **只收窄载荷、不收窄可执行集合**                    → 红（5 failed）
  **首轮有一处打偏，值得记**：(c) 最初把 help 的**一个**信号换掉——help 还有别的
  信号，`unrouteable_domains()` 因此仍为空、用例**正确地不红**。变异打偏与用例漏测
  在输出上长得一样（都是绿），**必须先确认变异真的落在了「缺陷形态」上**，否则会把
  自己的失误记成守则不灵——这正是 04c 记过的同一类坑（它首轮 3 处未命中全是覆盖
  缺口，本次这一处是变异不达靶）。
数据/同步/AI/安全影响：
  AI 协议面：**八类事件与其余七类的载荷一字未改**；`phase` 载荷新增两个纯增量字段
  （`domains` / `matched`，客户端可忽略）与一个新取值生产者（`routing`，P1 起就在
  枚举里）。
  **数据面零改动**：不建表、不迁移、不写事件日志。
  安全：三条铁律不放松——路由只改**注入面**，写路径、审批门、授权表、payload 校验
  一行未动；收窄后的 `_advertised` 是**新的隔离边界**（域外工具名一律
  `tool_unavailable`），本卡另加一条「路由外的工具不可执行」用例钉住它。
  模型上下文：不新增任何模型可见的文本（词表只进代码，`signals` 不回喂）。
兼容与回退方式：
  **回退＝把 `route_domains` 的返回值固定成全量域（一行）**，或按决策 6 显式传
  `tools`；无数据迁移、无契约替换、无开关。**旧客户端行为不变**：03c 的解析器本来
  就容忍 `routing` 帧（P2 起按占位渲染），多余字段一律忽略。
  兜底路径（`matched=False`）与 P1 的注入面**逐字相同**。
开放风险与责任人：
  ① **R-PROD-39（新增）**：词表覆盖度——手写词表，漏配**静默**退化为全量注入
  （用户无感），只有 `matched=false` 的比例能在生产上观察，而**当前没有采集方**
  （帧只到客户端、服务端不落日志）。责任人：P4 收口 / 生产统计；
  ② **R-PROD-17 已改写**：见上「实测」。责任人：已闭环（残余两项归收口）；
  ③ **05i 落地的 help 词表在词表里占位**：那一组信号是**问法**（支持 / 能不能 /
  可以吗）而不是名词，**误注入面比别组大**（会跟着「能不能把这一笔删了」这类纯操作
  请求一起注入）。取舍与 help 域的性质（1 个无数据访问的工具）有关，已记入 ADR-011
  残余 5；若生产上观察到噪声，收窄这一组即可。责任人：P4 收口。
并发记要（供下一卡参考）：
  ① 本卡施工期间，**05i（产品帮助）先在本工作区落地**（`a221b5e`）。两次碰撞都
  值得记：**（a）词表守卫的形态**——最初写成模块级 `raise`（「新增域必须同时补词」），
  05i 在 `tools.py` 加了第 15 个域而词表未补时，**整个测试套件十个模块 ERROR**
  （导入即失败）。这是「限制爆炸半径」的反面教材，已改为**用例守护**
  （`test_every_domain_has_at_least_one_signal` + `unrouteable_domains()`），失败
  被精确限制在一条用例上。**（b）跨卡共享文件**——05i 在本卡的 `routing.py`（新文件）
  里补了它的 help 词表、在 `stream.py` 里补了它的步骤卡文案；**它比本卡先提交**，
  那两处因此**已经是 HEAD 的一部分**。本卡提交时**不需要也不应该**摘掉它们——
  反过来，摘掉会让本卡的词表守卫在 HEAD 上变红。**教训**：跨卡共享文件在「后完成者
  负责解决冲突」之外还有一条更省事的路径——**先完成者把它需要的东西留在对方的文件
  里，由对方连同自己的改动一起提交**；判据是 `git show HEAD:<path>`，而不是看工作区。
  ② **验证必须在隔离树里跑**：主工作区在 05i 提交前后分别有「在途改动」与「他人
  提交」两种污染，本卡的四板数字因此取自 `git worktree`（分支 `t-m7-05g`，
  `server/.env` 拷一份）；1547 / 37 是 **HEAD（`a221b5e`）+ 本卡 7 个文件**的读数，
  基线 1520 也在同一棵树里实测。
  ③ **HEAD 上有一处与 05i 相关的既存红**：`ruff check src/ tests/` 报 3 处
  （`src/modules/ai/help.py` 与 `tests/test_ai_tools_help.py` 的文件末尾换行、
  `test_ai_tools_help.py` 的 import 排序），**由 `a221b5e` 带入**、与本卡零交集
  （本卡文件全绿）。按「不替其他卡修问题」未在本卡提交中改动，已在交付报告中如实
  记录——**它会让 server CI 的 ruff 步骤红**，需 05i 的会话或收口处置。
下一项未阻塞任务：P4 剩下 **05h（Skill 封装）**、**05j（偏好项上云 + `meta_set`
  服务端执行）** 与**批次 6 收口**；收口还欠两条裁决：R-PROD-38（提醒行的工具体）
  与 R-PROD-31（28 个写 op 的客户端渲染面）。
```
### 05i 产品帮助检索（2026-09-20，`a221b5e` + `64162be`）

```text
任务编号：T-M7-05i（P4 批次 5；原卡标注「可选，可砍」，本卡**纳入 P4**）
完成范围 / 明确未做：
  完成——① 范围按用户指令收窄：**只做产品帮助**，**理财知识（外部数据检索）砍掉不做**；
  ② help.py：28 条语料 + 确定性关键词检索（双向子串、同分按 id 排序、最多回 3 条）；
  ③ 工具侧接线：新域 help（15 域 / 55 工具）+ 读工具 help_search + stream.py 步骤卡文案；
  ④ 域路由（05g）的 help 组信号（问法形态，13 条）；⑤ 真实调用抽样探针 + 数据点。
  未做（刻意，非遗漏）——① **理财知识检索**（用户指令砍掉）；② 语料的**自动生成**
  （手写语料的漂移风险登记为 R-PROD-40）；③ 客户端任何改动（读工具、无新契约、无新事件）；
  ④ 帮助内容的**版本化 / 热更新**（语料在服务端代码里，改语料＝发版，与 prompt 热更新不同）。
  范围外的两条判断：**AI 自身能做什么**不进语料（那是模型的工具清单，它有一手信息，
  写进语料只会多一份会漂移的副本）；**「关于 → 检查更新」**不写进语料（该按钮当前是
  固定回「已是最新版本」的占位实现，写它等于承诺一个不存在的更新链路）。
修改的文档、契约与代码：
  server/src/modules/ai/help.py（新）、server/src/modules/ai/tools.py（+DOMAIN_HELP /
  +help_search / +导入）、server/src/modules/ai/stream.py（+1 行步骤卡文案）、
  server/src/modules/ai/routing.py（+help 组 13 个问法信号——**与 05g 同文件，见下**）、
  server/tests/test_ai_tools_help.py（新，50 例）、server/scripts/probe_help_selection.py（新）、
  docs/architecture/ai-agent-architecture.md（§7.2 加 help 行 + 表外说明）。
  契约面**零改动**（无新 op、无 schema / fixture / TS 校验器改动）。
验证命令与结果：
  cd server && python -m pytest tests/ -q                    → 1547 passed / 37 skipped（+50 例）
  cd server && python -m ruff check src/... scripts/...       → All checks passed
  cd server && python scripts/probe_help_selection.py         → 该命中组首调 10/10，误抢组 0/3
  （合并后 HEAD 上复跑：test_ai_tools_help.py + test_ai_routing_05g.py + test_ai_tools.py = 176 passed）
数据 / 同步 / AI / 安全影响：
  数据面：零改动（不建表、不写 sync change、不碰任何用户数据）。
  AI 面：工具集 +1（55），域 +1（15）。**每一步的 tools 载荷多一个工具**——但域路由
  只在命中问法信号或兜底全量时才注入它，且它体量很小（描述一句话 + 一个字符串参数）。
  安全：帮助语料**不含用户数据、不含 id**（用例钉住 UUID 形态即红）；工具是**只读**，
  不过审批门也没有执行面；「不写未来承诺」用例把「模型照着语料向用户承诺」这条路堵住
  （帮助是模型唯一的产品事实来源，这是本卡最该守的边界）。
  **理财知识砍掉**这一条在语料侧有一个正向守护用例（出现行情 / 收益率 / 投资建议类
  措辞即红），不是只写在文档里。
兼容与回退方式：
  **回退＝删掉 help 域**：无迁移、无契约替换、无开关。旧客户端同样拿到这个读工具
  （读工具与契约车道无关）——它不需要理解任何新东西；若某天要下线，把 DOMAIN_HELP
  从 DOMAINS 摘掉即可（工具随之不再注入，v1 / v2 两条车道一致）。
  语料改动是**纯服务端发版**，与客户端版本无关（这正是把语料放服务端而不是 App 内的理由）。
开放风险与责任人：
  ① **R-PROD-40（新增）：语料随实现漂移**——入口文案与能力边界只存在于这一份手写语料里，
     一句过期的话会被当成权威答案讲给用户。缓解＝用例钉住可自动化的那半 + 抽样表在改
     关键词时变红；**建议动 UI 入口的卡把「顺手复核对应语料」当成交付物的一部分**。
     责任人：未指派（P4 收口可裁决是否立卡）。
  ② **R-PROD-17 的新数据点（供收口用）**：本卡量到的是**注入面决定可达性**——补信号前
     4 条未中的共同形态是「路由根本没注入 help」，而**注入时 6/6 都调了**。也就是说
     「问法类域」的漏注入比数据域更隐蔽：数据域漏注入表现为「工具没给」，问法域漏注入
     表现为「模型凭印象答产品问题」。责任人：P4 收口。
  ③ **05g 的词表对「问法 + 名词」混合句仍会漏**：实测里「怎么删除账号」命中的是
     `账号 → account`（而非 `注销 / 删号 → security`）——本卡给 help 补了问法信号后
     help 进得来，但 **security 域仍缺席**（用户拿不到注销工具）。这是 05g 词表覆盖度的
     一个具体例子，归 R-PROD-39（域路由词表覆盖度），本卡不越界改它的名词表。
     责任人：P4 收口 / 后续卡。
并发会话记要（供下一卡参考）：
  本卡与 **05g 同时在主工作区施工**（同一份工作区、同一个 HEAD）。三条实测经验：
  ① **`routing.py` 是本卡唯一需要改的「别人的文件」**——新域不补词会让 05g 的
     `test_every_domain_has_at_least_one_signal` 变红。我在开工时就把那组信号写好，
     最终它随 05g 的提交（`049b62d`）一起进了 HEAD——**共享文件上的改动，归属会落在
     先提交者的提交里**；判据是 `git show HEAD:<path>`，不是看工作区。
  ② **共享文件（`stream.py` 的 +1 行、架构文档的 §7.2）用「按 HEAD 重建我这份」做局部
     暂存**：`git show HEAD:<path>` → 只叠加自己的改动 → `git hash-object -w --path=<path>`
     → `git update-index --cacheinfo` → `git commit`（不带路径）。提交里因此只有自己的
     hunk，同事在途的改动仍留在工作区、由他自己提交——**不夹带**。
  ③ **提交顺序决定 HEAD 自不自洽**：`tools.py` 的 help_search 与 `stream.py` 的步骤卡文案
     必须同一次进 HEAD，否则 `test_every_registered_tool_has_progress_text` 的反向断言
     （文案表里不能有注册表没有的工具）会红。本卡把它们放在同一个提交里。
下一项未阻塞任务：批次 5 的 **05g 已交付（`049b62d`）**，其后可开 **05h（Skill 封装，
  在途）** 与 **05j（偏好项上云）**；**P4 收口**欠三条裁决：R-PROD-38（提醒行的工具体）、
  R-PROD-31（28 个写 op 的客户端渲染面）、以及本卡登记的 R-PROD-40（语料漂移是否立卡）。
```


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