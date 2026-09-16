# ADR-006 AI Agent 架构升级

**状态：** 已采纳（2026-09-16；同日 v2 修订经账户所有者确认）
**范围：** M7（P0–P4），详见 `docs/milestones/production-execution-plan.md` M7 章节
**关联文档：** `docs/architecture/ai-agent-architecture.md`（目标架构全文）、`docs/architecture/ai-architecture.md`（当前已实现契约）

> **v2 修订说明：** v1（同日早些时候）提议「客户端 Host + 每步一次 HTTP」，论据是 `savings`/`scheduled`/`personas` 仅有本地数据、服务端够不到。v2 **反转为「服务端 Host + SSE 流式」**——因为该论据的前提（数据不上云）本身应当被打破：`D-PROD-03` 已确认「云端权威」，三实体上云是多设备/备份/换机恢复的既定需求，而非为 agent 付的额外成本。用户明确指定以 DeepSeek App 模式为标杆。v1 的客户端工具优势（零延迟、离线）在混合架构中由「设备能力通道」保留（§决策 1）。**该反转已于 2026-09-16 经账户所有者确认，本 ADR 转为「已采纳」。**
>
> **决策登记：** `APP-CONTEXT.md` §12 `D-PROD-12`。

## 背景

### 现状

当前 AI 链路为**单发制**：一次用户输入 → 一次 `/ai/interpret` → 一个 intent → 一个动作。三个天花板：

1. **单发制**——无法完成"先查上月预算再改"这类多步任务
2. **枚举制**——每新增表单能力都要改 prompt 主体白名单
3. **无上下文**——`AIInterpretRequest` 只有 `input`+`context`，无 history；用户可感知的后果是二次澄清、补充说明、指代全部失效

### 目标

用户通过一个对话框完成 **App 内全部已有功能**（25 个页面、约 70 个 bridge 命令覆盖的全部表单任务），且新增功能不需要改动 prompt 主体。

### 标杆与调研结论

对标 **DeepSeek App**。调研结论（详见 `ai-agent-architecture.md` §2）：

- DeepSeek App 官方技术架构**无公开资料**；「loop 在服务端」是基于工具位置的**推断，非事实**
- 已核实的可借鉴实现是 **DeepSeek Harness（DSH）**（2026-08-13 开源，MIT，Developer Preview）：Cordis 微内核 + 一切皆插件、Session V3 事件日志、审批四值、Turn/Step 分层、退出条件硬边界
- **规律：loop 跟着工具走**——工具在云端则 loop 在服务端（对话类 AI App），工具在本地则 loop 在客户端（Claude Code/Cursor 类）

### 已就绪的既有资产

- bridge 约 70 个命令，覆盖全量 CRUD（`app/src/platform/bridge/bridge-types.ts`）
- ActionProposal 契约 + 前后端同结论校验器
- 确认卡 / 操作预览卡（`app/src/features/assistant/`）
- 本地 NLU 降级链路；`AIConversation` 表已建（含 `summary` 字段）
- 桥事件推送机制（`onPermissionResult` 的「pending + 事件」模式，`native-bridge.ts:497`）——SSE 转发的现成基础
- OkHttp 4.12 已在依赖（仅需加 `okhttp-sse`）

## 决策

### 决策 1：编排位置——服务端 Host + SSE 流式（v2 反转）

```
客户端 = 瘦渲染 + 设备能力执行器 + 离线兜底
     ↕ SSE 下行（流式事件） + HTTP 上行（输入/工具结果/审批）
服务端 = Agent Host（loop、工具执行、上下文、配额审计）
```

| 项 | 选择 | 备注 |
|---|---|---|
| Loop 位置 | **服务端**（FastAPI） | 能力更新不发版；风控与成本一处收口 |
| 流式 | **SSE**（`text/event-stream`） | Kotlin 层 OkHttp `EventSourceListener` 接收 → 桥事件转发给 WebView；不依赖 WebView fetch streaming |
| 客户端职责 | 流式渲染、会话列表、设备能力执行、确认卡、离线兜底 | **不含 agent loop / 工具选择 / 上下文编排** |
| 上行通道 | 复用 `ai.*` 桥命令族 + HTTP | Token 不出 Kotlin 层（ADR-003 不变） |
| 设备能力 | 单向指令（不回传）/ 需结果（回传，loop 暂停等待） | 见 `ai-agent-architecture.md` §4.3 |
| 会话状态 | 服务端持有（`conversation_id`），客户端只缓存 UI 投影 | 客户端不重传历史 |

**前提：** `savings` / `scheduled` / `personas` 三实体上云（§决策 2 的前提工作）。理由见背景「v2 修订说明」。

### 决策 2：会话存储——事件日志 + 派生投影

会话存为 **append-only 事件日志**，模型可见的 messages 只是从日志派生的**投影**（借鉴 DSH Session V3，多源交叉验证）。

| 项 | 选择 |
|---|---|
| 事实来源 | SessionEvent 日志（`seq` 单调递增，append-only） |
| 模型上下文 | 仅「Surface 事件」子集进上下文；结构性事件不占 token |
| 压缩 | `replace` 操作替换为摘要；**原文保留在日志中** |
| 能力 | 断连恢复、会话分叉、回放、审计 |
| 客户端 | `assistant-chat-store` 降级为「投影 B（UI 展示）」缓存 |

**理由：** 单纯的「存聊天记录」无法同时满足恢复、分叉、压缩无损、审计四项需求；而事件日志让这四项由同一个数据源派生。

### 决策 3：交互模型——统一智能模式，路径对用户透明

DeepSeek App 已于 2026-09-10 将三模式合并为**统一智能模式**（系统自动判断场景与难度）。Aozora 对应：

| 路径 | 覆盖 | 用户感知 |
|---|---|---|
| 快路径（保留现状） | 记账、查账、闲聊等单一意图 | 无步骤展示，直接给结果 |
| Agent loop（新增） | 复合任务、多步操作 | 步骤卡片流可见 |

**核心原则：复杂度对用户不可见。** 用户不需要选择「模式」；路径切换由系统自动判定。快路径保持 12 s / 25 s SLO 不变。

### 决策 4：写操作安全边界——审批四值 + 铁律不变

tool calling 的参数**仍然只是提案**：

```
模型 tool_call 参数 → 组装 ActionProposal（名→id 服务端确定性解析）
  → 校验（Schema + 上下文）→ 审批 → 执行（TOCTOU 重校验）
```

**铁律三条（不放松）：** ① 模型永不输出 UUID；② 金额与统计由确定性代码计算；③ 写库必经校验与审批门。

**审批四值**（借鉴 DSH，比二值确认更严谨）：`allowed-once`（唯一放行值）| `rejected` | `cancelled` | `unavailable`（视为拒绝）。**不确定时倾向拒绝**；无「默认同意」「记住选择」类隐式授权。

**服务端 loop 的额外收益：** 工具与数据同在服务端，校验与执行可合并为一次事务，**TOCTOU 窗口消失**；同时 **R-PROD-14（写操作绕过审计）显著缓解**。

### 决策 5：provider 与协议策略（不变）

| 项 | 选择 | 理由 |
|---|---|---|
| Provider | **保持 DashScope（qwen3.7-flash）** | OpenAI 兼容模式支持 function calling；国内网络与合规无风险 |
| MCP | **暂不引入真 MCP** | 无第三方工具接入需求；先按架构模式设计内部接口 |
| 向量 RAG | **首发不引入** | 用户数据是结构化的，SQL/API 更准更便宜可审计 |
| DeepSeek Harness | **仅作设计参考，不作依赖** | Developer Preview，官方明确不建议生产使用 |
| Skill | 按需加载的任务模板 | 解决「枚举制」天花板 |

**若未来引入其他 provider（含 Claude 等）：** 按 `AGENTS.md`「技术栈替换必须先通过 ADR」单独决策，不在本 ADR 范围。

## 拒绝的替代方案

1. **客户端 Host + 每步一次 HTTP（v1 的提议）**——工具在本地零延迟、离线可用是优点，但代价是：能力更新要发版、多步任务需多次 HTTP 往返、客户端要维护 loop 状态机与工具注册表、配额与风控可被绕过。**且其立论前提（三实体不上云）与 `D-PROD-03`「云端权威」原则相悖。** 其有价值的「本地能力」部分由设备能力通道保留。

2. **纯服务端 loop、无设备通道**——无法覆盖设备类能力（权限、语音、分享、触感、提醒、小组件），达不到「对话完成全部功能」的目标。

3. **全量工具一次注入（约 70 个）**——工具数超过 10-15 个显著降低选择准确率。采用域分组 + 预路由。

4. **用向量库做用户账单检索**——用户账单是强结构化数据，SQL 聚合更准、更便宜、可审计。向量 RAG 仅保留给帮助文档（P4 可选）。

5. **引入 DeepSeek Harness 作为依赖**——Developer Preview，官方不建议生产使用，接口高速迭代。仅借鉴其设计（Session 事件日志、审批四值、Turn/Step、退出边界）。

6. **直接替换 provider 为海外模型**——面向中国大陆用户（D-PROD-06），网络与合规存在实际障碍；现有 provider 已满足需求。

7. **保持单发制、仅扩 config op 白名单**——短期可覆盖部分表单任务，但仍是枚举制，且无法多轮。可作过渡，不作目标。

## 后果

### 正向

- ✅ **对话覆盖全部功能**——25 个页面、约 70 个命令映射为工具（含设备能力通道）
- ✅ **二次澄清与补充说明可用**——服务端持有会话状态，多轮自然成立
- ✅ **扩展性**——新增能力 = 新增工具或 skill，不改 prompt 主体
- ✅ **安全边界增强**——TOCTOU 窗口消失；R-PROD-14 显著缓解；审批四值比二值更严谨
- ✅ **能力更新不发版**——agent 逻辑与工具在服务端
- ✅ **UX 对齐标杆**——SSE 流式、步骤可见、统一智能模式、多会话
- ✅ **SLO 不退化**——快路径保持 12 s / 25 s；慢路径单步独立计时
- ✅ **离线记账不受影响**——记账路径本就是本地 NLU + 离线队列，不走 agent

### 负向 / 需要处理

- ⚠️ **三实体上云是硬前提**——`savings`/`scheduled`/`personas` 需建表、迁移、接入同步（好在独立价值明确，非 agent 专属成本）
- ️ **离线 agent 不可用**——断网时仅本地 NLU 兜底（仅覆盖记账）；设置类任务需联网。这是服务端 loop 模式的固有代价，已评估为可接受
- ️ **服务端复杂度上升**——loop、工具注册表、事件日志、SSE 端点均在服务端
- ️ **成本上升**——工具定义 + 多轮历史使 token 成倍增长，配额需改「每会话轮数 + token」双限（R-PROD-15）
- ⚠️ **SSE 长连接可靠性**——弱网/后台中断需重连与状态恢复（R-PROD-19）
- ️ **退出条件必须含硬边界**——否则 loop 可能「永不停止 / 假完成 / 半途而废」（R-PROD-16）
- ️ **`AIConversation` 首次投产 + 事件日志新表**——需验证迁移、清理与隐私边界（日志含用户自然语言）
- ⚠️ **设备通道是架构增项**——比纯对话类产品多一条通道，需协议与超时设计

### 需要更新的文档

| 文档 | 更新内容 | 状态 |
|---|---|---|
| `docs/architecture/ai-agent-architecture.md` | 目标架构全文 v2 | ✅ 已完成 |
| `docs/APP-CONTEXT.md` | §6.5 指向本文档；§9 M7；§11 加载表 | ✅ 已完成 |
| `docs/milestones/production-execution-plan.md` | M7 章节（任务拆分与验收） | ✅ 已完成 |
| `docs/risk-register.md` | R-PROD-15 ~ 19 | ✅ 已完成 |
| `docs/architecture/ai-architecture.md` | ActionProposal v2 契约（落地时） | ⏳ P1 |
| `contracts/action-proposal/schema.json` | v2（落地时） | ⏳ P1 |