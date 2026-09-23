/**
 * T-M4-02c: 助手对话状态机。
 *
 * 第八轮 Bug-6（用户决策：免确认本地记账）：
 *   输入 → 本地 NLU / AI Gateway 路由 →（在线时 best-effort 服务端验证）
 *        → **直接本地 insertTransaction 写入** → 卡片常驻可编辑 → 5s 撤销窗
 *
 * 写入权威从服务端 /ai/execute 移到本地 + pending_mutations 队列上行最终一致，
 * 离线可用，绕开真机未同步时的 "HTTP 404 Book not found"。
 * ai.execute / ai.validate 桥命令保留（图片批量链路 BatchConfirmationCard 仍在用
 * executeProposal），本 hook 不再调用 executeProposal。
 *
 * 降级策略（ADR-003 §6.4）：
 *   - 断网 → 本地 NLU
 *   - AI 超时/503/429 → 本地 NLU
 *   - 本地 NLU 无法解析 → chat 兜底
 *
 * 第十二轮 T12-11（REQ-37 / 决策 #2 #3 #5）：interpret 输出 v2 intent 分发矩阵——
 *   bookkeeping → 现链路；chat/query → 文本气泡（query 本地直算优先，LLM 无数据权限
 *   不得编数字）；edit/delete → pendingOp 圈定候选交 OpPreviewCard 预览确认，
 *   confirmOp 逐笔 update/softDelete（上限 50）。needsAI 判据不翻向（决策 #2）；
 *   离线/无 key/失败 → edit/delete/query/chat 一律 canned 降级，bookkeeping 保本地 NLU。
 *
 * 第十四轮 T14-4（REQ-42 / 决策 #7）：messages 持久化到 platform/store/assistant-chat-store
 *   （zustand persist → localStorage），切页 remount 历史不丢；瞬时态不落盘。
 *
 * 第十三轮 T13-5（REQ-48 / 决策 #6 #7 #8）：分发矩阵 +intent=config——config 坏形状
 *   回落 chat 转述 reply；好形状 → resolveConfigTargets 名→id 解析（失败引导语不开卡），
 *   成功 → pendingConfig 预览确认卡（决策 #8 rows 模式），confirmConfig 逐 op 走现有
 *   桥命令（Kotlin 零改动），cancelConfig 不落写；context 注入 +books/accounts/
 *   budgets/goals 现有对象名单段（buildAiObjectNames ≤30 条 × ≤16 字，决策 #7）。
 *
 * 第十五轮 T15-7（REQ-56 / 决策 #8）：edit 契约 v4 客户端侧——openOpPreview 对
 *   filter/set 点名的账户/账本先过名→id 解析（matchAccountByName/matchBookByName）：
 *   filter 侧指代不明 → 引导语不开卡（绝不回落当前账本盲猜）；命中他账本 → 跨账本
 *   取数圈定；set 侧匹配不到 → 该字段剔除不回写 + 引导语（宁缺勿错）。confirmOp
 *   把 accounts/books 名单下传 buildEditUpdates（type/账户/账本全字段消费）。
 *
 * T-M7-02i（P1 客户端消费最小形态）：needsAI 的输入在**开关开启且桥具备流式能力**时
 *   改走 ai.agent（服务端 agent loop 的 SSE 通道）——流式期间只渲染文本气泡，done 到达
 *   后按既有 v2 分发矩阵走原有卡路径（用户感知为「思考完毕展开卡」）。
 *   - **v2 intent 在流式中无法预知**（chat/query/edit/delete/config 的 UI 形态互异），
 *     所以流的中间态不做 intent 判定，只有 done 的产物决定落点（架构 §9.1：复杂度对
 *     用户不可见）。
 *   - **职责边界：** 不做步骤卡片流、不做多会话、不做模式选择（归 P2 / T-M7-03）。
 *     三个互斥瞬时态（activeProposal / pendingOp / pendingConfig）的约束不变。
 *   - **桥命令由 T-M7-02h 提供，本卡只消费**：`bridge.runAgent` / `bridge.onAiStream`
 *     （取消的 `bridge.cancelAgent` 协议已就绪但本卡未接线——P1 无 UI 触发点；
 *     接线见下方 T-M7-03b 段落）。
 *     本文件用**结构化窄接口 + 运行时能力探测**取用它们（见 `AgentStreamBridge`），
 *     因此桥未落地或 APK 版本落后时自动回落，不需要与 02h 并行改同一批文件。
 *   - 开关关闭**或桥不具备能力** → 整条回落既有 ai.interpret 单发链路，12 s / 25 s 体验不变。
 *
 * T-M7-03b（P2 客户端基座）：流式消费段**搬去 `use-assistant-stream.ts`**——事件 → 状态的
 *   纯 reducer（八类事件全消费，投影态含步骤/挂起提案/设备指令）与订阅收发的 hook 分离，
 *   本文件只留编排与卡流程。三处随之变化：
 *   - 会话 id 由内存 ref（切页 remount 即丢）**提升到 store**（`activeConversationId`），
 *     `done.conversation_id` 交回时用 `adoptConversation` 把当前会话固化到那个 id 上；
 *   - `ai.cancel` **接线**（P1 只有桥协议没有调用点）：新增 `cancelStream()`，
 *     取消后三个互斥瞬时态收敛为空且零写（03e 的「停止生成」按钮接它）；
 *   - messages 按会话分桶（`assistant-chat-store` v2），会话切换互不串扰。
 *   职责边界不变：**重构只搬家不换语义**，三个瞬时态的约束、v2 分发矩阵、快路径
 *   12 s / 25 s 体验一律照旧。
 *
 * T-M7-03e（P2）：**确认门的四值与取消 / 打断收敛**——服务端只产出挂起提案、从不自己等
 *   回答（02g 决策 2：桥没有上行审批通道），所以审批四值（架构 §7.4）落在这里，含义是
 *   「用户对那张卡的处置」：`allowed-once`（卡上确认，执行后即失效）/ `rejected`（拒绝，
 *   零写、任务继续）/ `cancelled`（停止生成 / 语音打断，零写、任务中止）/ `unavailable`
 *   （失败或断连时仍未回答的提案 → **视为拒绝**、零写）。三处要点：
 *   - **事件驱动 + 权威把关**：`confirm` 帧到达即出卡（用户提前看见 agent 想做什么），
 *     但 `done.proposals` 才是权威清单——收尾时核对队列，不认中途来的孤提案；
 *     动作**只在收尾后启用**（UI 禁用 + `editProposal` 的第二道防线）。
 *   - **挂起提案成队列**（一轮可挂多张，逐张处置），**已处置集合**挡住收尾时把已拒绝的
 *     提案补回队列。
 *   - **中断态**（R-PROD-19）：失败 / 断连 → `interrupted` + 重连入口（P2 的手段是原样
 *     重发；按 seq 续跑归 P3 的 04b），且与「取消」**分成两种说法**（主动 vs 被动）。
 *   职责边界：服务端一行未动、桥无新命令、无新事件类型。
 */

import { useState, useCallback, useRef, useEffect, useMemo } from 'react'
import type { ActionProposal, ActionProposalV2 } from '../../domain/action-proposal'
import { isV2Proposal } from '../../domain/action-proposal'
import { parseInput, type ParseContext } from '../../domain/nlu/parser'
import {
  buildProposalFromAiOutput, parseAiInterpretOutput, hasOpConditions, applyOpFilter,
  buildEditUpdates, sumByFilter, MAX_BATCH_OPS,
  resolveConfigTargets, buildAiObjectNames, buildConfigPreview, deadlineToSavingsPeriod,
  matchAccountByName, matchBookByName,
  type AiConfig, type AiOpSet, type AiInterpretV2, type ConfigTargets,
} from '../../domain/ai-proposal-builder'
import { useBridge } from '../../platform/hooks/use-bridge'
import { useAssistantChatStore } from '../../platform/store/assistant-chat-store'
import {
  useAssistantStream,
  type AgentStreamBridge, type AgentStreamProjection, type StreamingBubble,
} from './use-assistant-stream'
// T-M7-04e：中断轮的文案与重建侧共用一份（同一件事只有一句说法）
import { INTERRUPTED_TURN_NOTE } from './history-projection'
import { newUuid } from '../../platform/uuid'
import { todayBizDate } from '../../domain/date'
// REQ-69（T18-6）：余额（可负）的显示口径，与账户页同一 helper
import { formatSigned } from '../../domain/money'
import { META_KEY_DEFAULT_ACCOUNT_ID, META_KEY_DEFAULT_BOOK_ID } from '../../domain/meta'
import type {
  AccountData, BookData, BudgetData, CategoryData, SavingsGoalData, TransactionData,
} from '../../platform/bridge/bridge-types'
import type { AiInterpretContext, AiValidateVerdict } from '../../platform/bridge/bridge-types'
import type { DeviceRequestFrame } from '../../platform/bridge/bridge-types'
import type { Category } from '../../domain/types'
// T-M7-05f：设备能力通道的客户端执行器（能力名 → 桥调用 + 回传编排）
import {
  VOICE_CAPABILITY,
  deliverDeviceResult,
  runDeviceRequest,
  type DeviceExecutorBridge,
} from './device-executor'

// T-M7-03b：流式契约与接线搬去 use-assistant-stream.ts；这几个名字从这里转出，
// 是为了不动既有 import 面（02i 的用例与后续卡片都从本文件取类型）。
export type {
  AgentStreamBridge, AgentStreamDone, AgentStreamEvent, AgentStepView,
  AgentStreamProjection, StreamingBubble,
} from './use-assistant-stream'

export interface AssistantMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  proposal?: AnyProposal
  proposalStatus?: 'pending' | 'confirming' | 'executing' | 'committed' | 'undone' | 'error'
  transactionIds?: string[]
  verdict?: AiValidateVerdict
  validationErrors?: string[]
}

/** 第八轮：'confirming' 态随免确认改造移除——解析成功后直接本地提交。 */
export type AssistantPhase = 'idle' | 'processing' | 'executing'

/** T16-7（收口修复）：一笔交易实际写入后的生效字段——回填进消息内提案，
 *  使只读历史卡显示真正落库的账户，而不是回退读页面默认值。 */
interface WrittenTx {
  id: string
  accountId: string | null
}

/** T16-7（收口修复）：把实际写入的账户回填进消息内提案。
 *  提案本身只是草稿（未点名账户时字段缺省），只读卡按 `accountId ?? 页面默认` 渲染，
 *  而页面默认在卡片挂载时可能尚未就绪（异步 meta 读）→ 历史卡会显示「不使用账户」，
 *  与库内实际写入的账户不符；且默认账户日后被改，旧卡显示的账户也会随之漂移。
 *  回填后历史卡与落库值恒等。 */
function withResolvedAccounts(proposal: ActionProposal, written: WrittenTx[]): ActionProposal {
  const txs = proposal.transactions ?? []
  if (txs.length === 0 || txs.length !== written.length) return proposal
  return {
    ...proposal,
    transactions: txs.map((t, i) => ({ ...t, accountId: written[i].accountId })),
  }
}

/** 常驻编辑卡的展示状态（error 态下卡片仍可编辑，改动即重试保存）。
 *  T12-8（REQ-30 / 决策 #7）：pending-category = 提案缺分类，未写库，卡内选定后接管首次写入。
 *  T-M7-02i：pending-confirm = agent 路径的**挂起提案**（服务端 requiresConfirmation），
 *  未写库，卡内确认后才触发首次写入。与 pending-category 同为「未写库」态，差别在
 *  触发写入的条件：前者要补分类，后者只需用户点头（契约见 ConfirmationCard 的
 *  handleConfirmEdit：pending-confirm 态下无变更也执行写入）。 */
export type CardStatus = 'executing' | 'committed' | 'error' | 'pending-category' | 'pending-confirm'

/**
 * T-M7-03e：确认卡上的**审批四值**（架构 §7.4）在客户端的落点。
 *
 * 服务端 loop 只产出「挂起提案」（02e 的 `AgentResult.proposals`），审批回答落在这里——
 * 客户端没有上行审批通道（02g 决策 2：桥只有 `ai.agent` / `ai.cancel`），所以四值表达的是
 * **用户对这张卡的处置**：
 *
 * | 值 | 触发 | 效果 |
 * |---|---|---|
 * | `allowed-once` | 卡上点「确认」 | 执行写入；执行后即失效（不产生长期授权） |
 * | `rejected` | 卡上点「拒绝」 | **零写**；这一项不改，任务不因此中止 |
 * | `cancelled` | 输入区「停止生成」/ 语音打断 | **零写**；整个任务中止（架构 §4.4 不变式 3：收敛到一致状态） |
 * | `unavailable` | 本轮以失败 / 断连收尾时仍挂着未回答的提案 | **视为拒绝**、零写（架构 §7.4：不确定时倾向拒绝） |
 *
 * 「取消」是用户主动、`unavailable` 是被动——两者在 UI 上是两种说法（**不合并成
 * 一个状态**），恢复路径也不同：前者用户自己按的、后者要给重连入口（R-PROD-19）。
 */
export type ApprovalOutcome = 'allowed-once' | 'rejected' | 'cancelled' | 'unavailable'

/**
 * T-M7-03e：一张挂起的写提案（确认门的队列元素）。
 *
 * `step` = 服务端产出它时所在的 Step 序号（1 起，`confirm` 帧载荷），仅作记录——
 * 审批策略（单 Step 自动 / 多步强制）是**服务端**的判定，客户端不重算。
 */
export interface PendingApproval {
  proposal: AnyProposal
  step: number | null
}

/**
 * 尾卡（R-PROD-31）：**两条车道的提案**都可能是挂起态的那一张。
 *
 * `contractVersion` 在场与否就是车道判据（与 `validateProposalAny` 同一把尺子）——
 * 服务端为一个不认识 v2 的旧客户端降级产出 v1，本客户端两条都得能收。
 */
export type AnyProposal = ActionProposal | ActionProposalV2

/** 车道判定见契约层（`domain/action-proposal.ts` 的 `isV2Proposal`）——本文件
 *  只是转发它，方便 `use-assistant` 的既有调用点少一个 import。 */
export { isV2Proposal } from '../../domain/action-proposal'

/**
 * 尾卡（R-PROD-37）：**设备执行 op → 桥命令**的分派表。
 *
 * 这四个 op 的服务端**只挂起不执行**（`tools.DEVICE_EXECUTED_OPS`）——它们写的
 * 是设备本地的状态（当前角色指针 / 隐私同意 / 同步触发 / 提醒调度），服务端没有
 * 落点。所以客户端必须自己把它们落到桥命令上，否则表现就是 R-PROD-37 记的那个
 * 形态：确认卡点下去、回执说成功、实际什么都没发生。
 *
 * 表里的四条桥命令**都已存在**（`native-bridge.ts` 的 `setMeta` / `recordConsent` /
 * `triggerSync` / `setReminder`），本卡不需要新增通道——因此**不触桥命令计数面**。
 *
 * 参数形状与 `tools.py` 里各工具体产出的 `payload` **逐字对应**：那一侧按同一份
 * 定义写，中间不做翻译，两边因此不会漂移。
 */
/**
 * 设备分派表需要的那几个桥方法——**结构化窄接口**（同 `AgentStreamBridge` 的做法：
 * 只声明用到的部分，不要求整个 bridge 类型）。这样表可以写在 hook 外面，
 * 也不必把 `useBridge()` 的具体类型引进来。
 */
export interface DeviceOpBridge {
  setMeta(key: string, value: string): Promise<unknown>
  recordConsent(): Promise<unknown>
  triggerSync(): Promise<unknown>
  setReminder(enabled: boolean, hour: number, minute: number): Promise<unknown>
}

export const DEVICE_OP_DISPATCH: Record<
  string,
  (payload: Record<string, unknown>, bridge: DeviceOpBridge) => Promise<unknown>
> = {
  persona_switch: (p, bridge) => bridge.setMeta('active_persona_id', String(p.personaId ?? '')),
  consent_record: (_p, bridge) => bridge.recordConsent(),
  sync_trigger: (_p, bridge) => bridge.triggerSync(),
  reminder_set: (p, bridge) =>
    bridge.setReminder(Boolean(p.enabled), Number(p.hour), Number(p.minute)),
}

/**
 * T-M7-03e：中断状态（R-PROD-19）——agent 轮以失败 / 断连收尾时的**重连入口**数据。
 *
 * P2 只做「提示 + 入口」：`input` 是本轮的用户输入，用户点「重发」即原样再发一次。
 * 按 `conversation_id` + `seq` 的续跑归 P3 的 04b（架构 §8.3）。
 * **瞬时态**，不落盘（与 processing / pendingOp 同口径）。
 */
export interface InterruptedTurn {
  /** 本轮的用户输入（重发用） */
  input: string
}

/**
 * T12-9（REQ-35）：聊天卡编辑态卡内「账本/预算」chip 的带外覆盖。
 * ActionProposal 契约（contracts/action-proposal/schema.json）不含这两个字段，
 * 写库时才注入——bookId 缺省用 hook 层账本；includeInBudget 缺省不传 →
 * native 默认计入（与聊天路径历史行为一致，第十轮 REQ-17 口径）。
 */
export interface ProposalWriteOverrides {
  bookId?: string
  includeInBudget?: boolean
}

/**
 * T12-11（REQ-37 / 决策 #5）：edit/delete 意图的待确认操作（OpPreviewCard 消费）。
 * candidates = filter 在当前账本本地圈定的全集（bizDate DESC）；
 * 执行截断 MAX_BATCH_OPS、预览截断 OP_PREVIEW_ROWS 由消费方处理。
 */
export interface PendingOp {
  kind: 'edit' | 'delete'
  /** 云端操作结果拟语（成功且无失败/截断时优先作为回执文案；可空） */
  reply: string
  /** edit 的新值（delete 恒 null） */
  set: AiOpSet | null
  candidates: TransactionData[]
}

/**
 * 第十三轮 T13-5（REQ-48 / 决策 #6 #8）：config 意图的待确认设置操作。
 * targets = resolveConfigTargets 已解析的本地 id；preview = OpPreviewCard config
 * 模式内容（决策 #8 rows 形态）。确认前零写入；「取消」不落任何写。
 */
export interface PendingConfig {
  cfg: AiConfig
  /** 云端对即将所做设置的简短说明（回执在预览后，拟语仅作气泡补充，暂存备用） */
  reply: string
  targets: ConfigTargets
  preview: { title: string; rows: Array<{ label: string; value: string }> }
}

// ───────────────────────────────────────────────────────────────────────────
// T-M7-02i / T-M7-03b：agent 流式通道的消费契约
//
// 契约类型（`AgentStreamEvent` / `AgentStreamDone`）的源在 `bridge-types.ts`；
// 窄接口 `AgentStreamBridge`、投影态 `AgentStreamProjection` 与收发的 hook 在
// `use-assistant-stream.ts`。本文件只保留**开关**——它是客户端灰度的一部分，
// 默认值与服务端同口径（P2 各卡不得改动，见 T-M7-03 计划 §五 第 4 项）。
// ───────────────────────────────────────────────────────────────────────────

/**
 * T-M7-02i：客户端层灰度开关（T-M7-02g 决策 6 交办给本卡落地）。
 *
 * 架构 §11.3 的双层灰度 = 服务端 `ai_agent_enabled` + 本开关。关闭时整条回落既有
 * `ai.interpret` 单发链路（12 s 慢速提示 / 25 s 取消的体验逐字不变）。
 *
 * **默认关闭**：与服务端同口径的灰度默认值。开启前须确认服务端开关已开、且本机
 * APK 是带 `ai.agent` / `ai.cancel` 命令的版本——桥命令表里没有这两条时，
 * `runAgent` 会以桥错误 reject，本轮直接按失败收尾（不会静默回落）。
 *
 * ⚠️ 2026-09-20（M7 收口轮）：**临时置 true 做真机联测**，测后按裁决恢复。
 */
export const AI_AGENT_CLIENT_ENABLED = true

interface UseAssistantOptions {
  categories: CategoryData[]
  bookId: string | null
  timezone: string
  /** T9-7（REQ-15）：默认账户 id（meta.default_account_id 优先，AssistantPage 经
   * pickDefaultAccount 解析后传入）；提案未指定账户时落库兜底用它。 */
  defaultAccountId: string | null
  /** T11-6（REQ-27 / 决策 #7）：用户画像（meta.user_profile 解析后的非空字段集）；
   * 在线 AI 路径拼入 context.profile，本地 NLU 路径忽略。 */
  profile?: Record<string, string> | null
  /** T11-7（REQ-28 / 决策 #7/#8）：当前聊天角色 {name, text}；非空即拼入
   *  context.persona（服务端 render_messages_with_context 反注入前缀后追加 system 尾）。
   *  离线本地 NLU 路径忽略——人设仅在线 AI 网关生效（已知限制）。 */
  persona?: { name: string; text: string } | null
  /** T12-11（REQ-37 / 决策 #3）：分类名白名单（buildAiCategoryNames 截断产物），
   *  经 context.categories 注入，prompt v2 约束分类输出从列表选择。空/缺省 → null 段。 */
  aiCategories?: string[] | null
  /**
   * T13-5（REQ-48 / 决策 #7）：现有对象全列表（AssistantPage 装载后传入）。
   * 双职责——① context.books/accounts/budgets/goals 名单段（buildAiObjectNames
   * 截断，模型点名/避重依据）；② config 执行的名→id 解析数据源。
   * 缺省/空 → 名单段 null + config resolve 走引导语。
   */
  books?: BookData[]
  accounts?: AccountData[]
  budgets?: BudgetData[]
  goals?: SavingsGoalData[]
  /** T13-5（REQ-48）：config 执行成功后的名单重拉回调——本轮新建的对象要能被
   *  下一轮点名/避重（活体发现：建账本成功后名单快照不刷新，"把默认账本设成
   *  旅行" 被拒）。取消/失败不触发（无写入，名单无需变）。 */
  onConfigApplied?: () => void
  /** T-M7-02i：agent 流式通道开关（02g 决策 6 的客户端层灰度）。缺省取
   *  `AI_AGENT_CLIENT_ENABLED`（默认关闭）。**开启也未必走成流式**——桥必须具备
   *  流式能力（见 AgentStreamBridge 的能力探测），否则静默回落单发链路。 */
  agentStream?: boolean
}

interface UseAssistantReturn {
  messages: AssistantMessage[]
  phase: AssistantPhase
  /** 常驻卡数据；解析成功后设置，撤销后清除。 */
  activeProposal: AnyProposal | null
  cardStatus: CardStatus | null
  isSlow: boolean
  /** T12-11：edit/delete 意图圈定后的待确认操作（null=无）；确认/取消后清空。 */
  pendingOp: PendingOp | null
  /** T13-5：config 意图解析成功后的待确认设置操作（null=无）。 */
  pendingConfig: PendingConfig | null
  /** T-M7-02i：agent 流式期间的气泡（null=不在流式中）。**内存态**，不进 messages
   *  store——流式期间由页面渲染，done/error 时定型成正式消息后置回 null。
   *  T-M7-03b：它是 `streamProjection` 的**气泡视图**（只取正文与推理两段）；
   *  03c 的步骤卡片流读 `streamProjection` 的 `steps`。 */
  streaming: StreamingBubble | null
  /** T-M7-03b：agent 流式期间的**完整投影**（null=不在流式中）——八类事件都落在
   *  这里（正文/推理增量、按 Step 归并的步骤、挂起提案、设备指令、结果与错误）。
   *  P1 只渲染正文与推理（`streaming`），步骤卡片流归 03c。 */
  streamProjection: AgentStreamProjection | null
  submitInput: (text: string) => Promise<void>
  /** T-M7-03b：取消当前 agent 流（`ai.cancel` 接线）。**零写**；取消后
   *  `activeProposal` / `pendingOp` / `pendingConfig` 一律收敛为空——
   *  用户按下停止，就不该再留一张待确认的卡（03e 的「停止生成」按钮接它）。
   *  T-M7-03e：它是审批四值的 `cancelled`——主动中止整个任务，与失败/断连（被动）
   *  是两种状态，故不合并。 */
  cancelStream: () => Promise<void>
  /** T-M7-03e：确认门上的「拒绝」（审批四值 `rejected`）——**零写**，任务不中止。
   *  没有挂在确认门上的卡时是空操作。 */
  rejectApproval: () => void
  /** T-M7-03e：中断态（失败 / 断连收尾）——null = 无中断。页面据此渲染提示与
   *  重连入口（R-PROD-19）。**与取消分开**：取消是用户按的，中断是被动的。 */
  interrupted: InterruptedTurn | null
  /** T-M7-03e：重连入口——把中断那一轮的输入原样重发（P2 的恢复手段；按 seq 续跑归 P3）。 */
  retryInterrupted: () => void
  /** T-M7-05f：等用户开口的设备语音请求（null = 无）。页面据此渲染提示，用户点既有的
   *  mic 按钮走完录音——**执行器不自动开麦**（T12-1 的语音按需授权口径）。 */
  deviceVoiceRequest: DeviceRequestFrame | null
  /** T-M7-05f：用户说完了——转写文本回传服务端，唤醒挂起的那一轮 loop。 */
  respondDeviceVoice: (text: string) => void
  /** T-M7-05f：这次语音输入没成（用户取消 / 录音失败）——按结局回传。不回传会让
   *  服务端白等满 `ai_device_timeout_seconds`（超时是它的收敛路径，但那是用户已经
   *  看得到的等待）。默认 `denied`。 */
  dismissDeviceVoice: (status?: 'denied' | 'failed') => void
  /** 卡片编辑保存：softDelete 旧笔 + insert 新（编辑 = 替换写）。
   *  T12-9（REQ-35）：overrides = 卡内账本/预算 chip 覆盖（首提与替换写均生效）。 */
  editProposal: (edited: AnyProposal, overrides?: ProposalWriteOverrides) => Promise<void>
  undoExecution: () => Promise<void>
  /** T12-11：执行 pendingOp（逐笔 update/softDelete，上限 50；部分失败可见）。 */
  confirmOp: () => Promise<void>
  /** T12-11：放弃 pendingOp（不执行任何写）。 */
  cancelOp: () => void
  /** T13-5：执行 pendingConfig（单桥命令，回执一条助手消息；失败可见不落半途）。 */
  confirmConfig: () => Promise<void>
  /** T13-5：放弃 pendingConfig（不落任何写）。 */
  cancelConfig: () => void
  /** T9-8（REQ-16）：页面级链路（图片批量卡）完成后追加一条助手文本消息。 */
  appendAssistantMessage: (text: string) => void
}

const CONFIDENCE_THRESHOLD = 0.8

// T12-12：操作/查询句式守卫（决策 #2 例外——这些句子本地解析必然错）。
// 覆盖 edit（改成/改为…）、delete（删/去掉/移除…）、query（查一下/多少…）。
const OP_SENTENCE_RE = /(改成|改为|记成|调成|删掉|删了|删除|删去|去掉|移除|查一下|查查|查询|多少)/
const SLOW_THRESHOLD_MS = 12_000

/**
 * 这一轮输入**是否必须交给云端 AI**；返回 false 表示走本地快路径直接记账。
 *
 * 抽成导出的纯函数是为了让它可被**离线核验**：这个判据决定了「一句用户输入会不会
 * 被本地 NLU 拦下来、AI 根本看不到」，是「AI 接管各类指令」这条路上最前置的一道
 * 闸门。测试集（``server/scripts/ai_tool_matrix``）需要对每条用例算出它走哪条路，
 * 而这个判据原先锁在 ``submitInput`` 内部、外部无从得知。抽出来**不改任何行为**——
 * 四条判据与原先逐字相同，只是从内联表达式变成一次函数调用。
 */
export function needsAiRouting(text: string, localProposal: ActionProposal): boolean {
  return OP_SENTENCE_RE.test(text)
    || localProposal.confidence < CONFIDENCE_THRESHOLD
    || localProposal.warnings.length > 0
    || localProposal.action === 'chat'
}

/**
 * T12-8（REQ-30 / 决策 #7）：Kotlin Repository 校验/查询失败的英文异常消息 → 中文。
 * 桥回传时 message 原样透出（如 insertTransaction 撞 `entity IDs are required`），
 * 按子串匹配（动态后缀如 `book not found: <id>` 只匹配前段）。
 * 表常量置于本文件头部导出，T12-10（TxEditSheet）/T12-11 复用；不建 i18n 机制。
 */
export const BRIDGE_ERROR_ZH: ReadonlyArray<readonly [string, string]> = [
  ['entity IDs are required', '缺少分类或账本信息，请在卡片中补全后保存'],
  ['category not found', '分类不存在，请重新选择分类'],
  ['book not found', '账本不存在，请返回首页切换账本后重试'],
  ['account not found', '账户不存在，请重新选择账户'],
  ['amountCents must be a positive', '金额不合法，请检查后重试'],
  ['invalid transaction type', '收支类型不合法，请重新选择'],
  ['invalid bizDate', '日期格式不正确，请重新选择日期'],
  ['transaction not found or already deleted', '该笔记录已不存在，可能已被删除'],
  // T12-10：update 乐观锁冲突（本地行版本已被同步 pull 等推进）
  ['transaction version conflict', '该笔记录刚被更新，请关闭编辑层后重新打开重试'],
  ['version must increase', '该笔记录刚被更新，请关闭编辑层后重新打开重试'],
]

export function localizeBridgeError(raw: string): string {
  for (const [needle, zh] of BRIDGE_ERROR_ZH) {
    if (raw.includes(needle)) return zh
  }
  return raw
}

/**
 * T12-8（REQ-30 ② / 决策 #9）：云端 interpret 失败一次性轻提示的文案映射（桥 code → 描述）。
 * 注意：handleAiInterpret 把服务端 503 统一压成 code='503'，响应体子码（ai_unavailable=
 * 未配置 key 或 provider 故障/超时）不透传，本链路无法区分「未配置」——no_ai_provider 分支
 * 预留给桥透传后启用（决策 #9 专属文案）；未知 code 回退「暂时不可用」。
 */
const AI_INTERPRET_ERROR_ZH: Record<string, string> = {
  no_ai_provider: 'AI 服务未配置：需在服务器 .env 填 AI_PROVIDER_API_KEY 并重启',
  '503': '云端 AI 暂时不可用',
  unavailable: '云端 AI 暂时不可用',
  timeout: '云端 AI 响应超时',
  network: '网络异常，云端 AI 未接通',
  '429': '云端 AI 请求过于频繁',
  auth_lost: '登录已失效，云端 AI 不可用',
}

/**
 * T12-11（REQ-37 ④）：云端不可达时 edit/delete/query/chat 的统一降级 canned
 * （决策 #2/#5：破坏性操作与闲聊都依赖云端意图识别，本地 NLU 只保留记账快路）。
 */
export const AI_OFFLINE_CANNED = '断网时我只能记新账哦，查询、修改、删除和闲聊要联网后再试试'

export function useAssistant({
  categories,
  bookId,
  timezone,
  defaultAccountId,
  profile = null,
  persona = null,
  aiCategories = null,
  books = [],
  accounts = [],
  budgets = [],
  goals = [],
  onConfigApplied,
  agentStream,
}: UseAssistantOptions): UseAssistantReturn {
  const bridge = useBridge()
  // T14-4（REQ-42 / 决策 #7）：messages 外置到 zustand persist store——切页 remount
  // 后历史仍在（写入时 ≤200 条截断，localStorage 读写静默容错）。setMessages 兼容
  // React updater 形态，下方几十处调用点零改动；processing/pendingOp 等瞬时态
  // 仍为内存 useState，不落盘（恢复后无过期撤销钮：卡由内存态门控）。
  const messages = useAssistantChatStore(s => s.messages)
  const setMessages = useAssistantChatStore(s => s.setMessages)
  // T-M7-03b：`done.conversation_id` 交回时把当前会话固化到该 id（P1 里这只是个内存
  // ref，切页 remount 即丢，重进就开新会话）
  const adoptConversation = useAssistantChatStore(s => s.adoptConversation)
  // T-M7-03d：陈旧 conversation_id 的降级（服务端 404 → 桥的 `not_found` 帧）
  const forgetConversation = useAssistantChatStore(s => s.forgetConversation)
  // T-M7-04e：跨设备按需重建的日志游标——收尾时落盘，`use-conversations` 拿它做增量补齐
  const setCursor = useAssistantChatStore(s => s.setCursor)
  const forgetCursor = useAssistantChatStore(s => s.forgetCursor)
  const [phase, setPhase] = useState<AssistantPhase>('idle')
  const [activeProposal, setActiveProposal] = useState<AnyProposal | null>(null)
  const [cardStatus, setCardStatus] = useState<CardStatus | null>(null)
  const [isSlow, setIsSlow] = useState(false)
  // T12-11（REQ-37）：edit/delete 意图的待确认操作卡
  const [pendingOp, setPendingOp] = useState<PendingOp | null>(null)
  // T13-5（REQ-48）：config 意图的待确认设置卡
  const [pendingConfig, setPendingConfig] = useState<PendingConfig | null>(null)
  // T-M7-03e：中断态（失败 / 断连）——页面据此渲染提示与重连入口（R-PROD-19）
  const [interrupted, setInterrupted] = useState<InterruptedTurn | null>(null)
  /**
   * T-M7-05f：等用户开口的设备语音请求（null = 无）。
   *
   * **不自动开麦**：模型下发 `voice.transcribe` 时替用户打开麦克风是不该有的默认
   * （T12-1 的语音按需授权口径要求由用户发起）。页面据此渲染一条提示，用户点那颗
   * 既有的 mic 按钮走完录音，收尾时由 `respondDeviceVoice` 回传转写文本。
   */
  const [deviceVoiceRequest, setDeviceVoiceRequest] = useState<DeviceRequestFrame | null>(null)
  /** 同上的同步镜像：回传要在事件回调里当场判断有没有挂着的语音请求。 */
  const deviceVoiceRef = useRef<DeviceRequestFrame | null>(null)
  /**
   * T-M7-03e：挂起提案的队列（未处置的那些，含正在展示的那张）。
   *
   * **不入 state**：队列本身的增减不直接渲染（渲染读 `activeProposal`），每次迁移都伴随
   * 一次 `setActiveProposal` / `setMessages`，靠它们触发重渲染即可。
   */
  const approvalsRef = useRef<PendingApproval[]>([])
  /** 已按四值处置过的提案 id（确认 / 拒绝 / 取消 / 未授权）——收尾核对与重复的
   *  `confirm` 帧都跳过它，否则已确认的卡会在 `done` 到达时「复活」出第二张。 */
  const handledApprovalsRef = useRef<Set<string>>(new Set())
  /** 确认门**当前展示**的是哪张提案（null = 卡不是确认门的卡）。收敛时要判断
   *  「被作废的那张是不是正挂在屏幕上」，不能一律清卡——那会连常驻卡一起清掉。 */
  const shownApprovalIdRef = useRef<string | null>(null)
  /** 本轮的用户输入（中断后「重发」用）。只由 agent 轮记录——快路径有既有降级链路。 */
  const lastTurnInputRef = useRef('')
  const slowTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // C-B3 fix: 并发提交互斥
  const isSubmittingRef = useRef(false)
  // T12-11：confirmOp 重入互斥（按钮禁用之外的最后防线）；T13-5 confirmConfig 共用
  const opBusyRef = useRef(false)
  // 当前卡对应的已写入交易 id（编辑=替换写 / 撤销=软删 都以此为准）
  const activeTxIdsRef = useRef<string[]>([])
  // T12-8（REQ-30 ①）：当前卡是否处于待选分类态（尚未写库；editProposal 据此接管首提）
  const isPendingCategoryRef = useRef(false)
  // T-M7-02i：当前卡是否处于待确认态（agent 挂起提案，尚未写库；语义同上一行）
  const isPendingConfirmRef = useRef(false)
  // T12-8（REQ-30 ② / 决策 #9）：云端失败轻提示会话级只弹一次，不轰炸
  const aiNoticeShownRef = useRef(false)

  // C-C2 fix: 组件卸载时清理所有 timer
  // （流事件的退订由 useAssistantStream 自己在卸载时兜底，不在这里重复）
  useEffect(() => {
    return () => {
      if (slowTimerRef.current) clearTimeout(slowTimerRef.current)
    }
  }, [])

  /** T-M7-02i：收尾的公共部分（清慢速提示 + 回 idle + 解除并发互斥）。
   *  原先内联在 submitInput 里，agent 流式路径也要用，故提为 hook 级。 */
  const finishProcessing = useCallback(() => {
    if (slowTimerRef.current) {
      clearTimeout(slowTimerRef.current)
      slowTimerRef.current = null
    }
    setIsSlow(false)
    setPhase('idle')
    isSubmittingRef.current = false
  }, [])

  // ───────────────────────────────────────────────────────────────────────────
  // T-M7-03e：确认门（挂起提案的四值处置）
  // ───────────────────────────────────────────────────────────────────────────

  /** 把一张挂起提案放进确认门队列。重复的 / 已处置的 → false（不成第二张卡）。 */
  const enqueueApproval = useCallback((proposal: AnyProposal, step: number | null): boolean => {
    const id = proposal.proposalId
    if (!id) return false
    if (handledApprovalsRef.current.has(id)) return false
    if (approvalsRef.current.some(a => a.proposal.proposalId === id)) return false
    approvalsRef.current = [...approvalsRef.current, { proposal, step }]
    return true
  }, [])

  /** 撤下确认门的卡（**只撤确认门那张**——不碰上一轮已落库的常驻卡）。 */
  const clearApprovalCard = useCallback(() => {
    isPendingConfirmRef.current = false
    shownApprovalIdRef.current = null
    setActiveProposal(null)
    setCardStatus(null)
  }, [])

  /** 展示队头的挂起提案（卡随即出现；**动作在收尾后启用**，见页面的 `approvalBusy`）。
   *  卡在流式期间就出现是**事件驱动**的那一半——用户越早看到 agent 想做什么越好
   *  （架构 §7.4：审批卡同时是安全门与纠偏点）。
   *
   *  同时保证它在**消息流里有一条记录**（已存在则不动）：回执文案（「已记录 N 笔交易」）
   *  与只读历史卡都挂在消息上，`editProposal` / `commitProposalInternal` 也按
   *  `proposalId` 匹配消息——队列里的第二张顶上时若没有这条消息，写入虽然照成，
   *  用户却看不到回执、历史里也没有它。文本先用提案自带的 `message` 兜底，
   *  收尾时 `settleStream` 再带正文调一次本函数把它换掉。 */
  const showPendingApproval = useCallback((text?: string) => {
    const head = approvalsRef.current[0]
    if (!head) return
    const pid = head.proposal.proposalId
    // 文本：收尾拿到正文就用正文；流式期间（还没有正文）先用提案自带的 `message` 兜底
    const display = text || head.proposal.message
    isPendingConfirmRef.current = true
    shownApprovalIdRef.current = pid
    setActiveProposal(head.proposal)
    setCardStatus('pending-confirm')
    setMessages(prev => {
      const i = prev.findIndex(m => m.proposal?.proposalId === pid)
      if (i < 0) {
        return [...prev, {
          id: 'msg_' + Date.now() + '_assistant',
          role: 'assistant' as const,
          text: display,
          proposal: head.proposal,
          proposalStatus: 'pending' as const,
        }]
      }
      // 已有这条（流式期间随 `confirm` 帧建过）→ 把兜底文案换成收尾后的正文，其余不动
      return text ? prev.map((m, k) => (k === i ? { ...m, text } : m)) : prev
    })
  }, [setMessages])

  /** 丢弃符合条件的挂起提案且**零写**，返回丢弃条数（调用方据此决定要不要提示用户）。
   *  丢弃的 id 一并记进已处置集合——否则服务端随后到的帧能让它们再成一张卡。 */
  const dropApprovals = useCallback((pred: (a: PendingApproval) => boolean): number => {
    const dropped = approvalsRef.current.filter(pred)
    if (dropped.length === 0) return 0
    for (const a of dropped) {
      if (a.proposal.proposalId) handledApprovalsRef.current.add(a.proposal.proposalId)
    }
    approvalsRef.current = approvalsRef.current.filter(a => !pred(a))
    const shown = shownApprovalIdRef.current
    if (shown && dropped.some(a => a.proposal.proposalId === shown)) clearApprovalCard()
    return dropped.length
  }, [clearApprovalCard])

  const buildParseContext = useCallback((): ParseContext => {
    const cats: Category[] = categories.map(c => ({
      id: c.id,
      ownerId: '',
      bookId: null,
      parentId: c.parentId,
      kind: c.kind,
      name: c.name,
      archivedAt: null,
      version: c.version,
    }))
    return {
      now: Date.now(),
      timezone,
      categories: cats,
      accounts: [],
    }
  }, [categories, timezone])

  /**
   * 第八轮：本地写入提案全部交易，返回生成的交易 id。
   * 聊天提案为单笔 add_tx；若将来出现多笔且中途失败，已写入部分不回滚
   * （与 QuickEntrySheet 手动记账语义一致——逐笔入队、失败可见）。
   */
  const writeTransactions = useCallback(async (
    proposal: ActionProposal, overrides?: ProposalWriteOverrides,
  ): Promise<WrittenTx[]> => {
    const written: WrittenTx[] = []
    const today = todayBizDate(timezone)
    for (const tx of proposal.transactions ?? []) {
      const id = newUuid()
      // 决策 #5（第七轮）对齐：非今日 → occurredAt 取该日本地 12:00
      let occurredAt = Date.now()
      if (tx.bizDate && tx.bizDate !== today) {
        const noon = new Date(tx.bizDate + 'T12:00:00').getTime()
        if (!Number.isNaN(noon)) occurredAt = noon
      }
      // T9-7（REQ-15）：提案未点名账户（字段缺失）→ 兜底默认账户；
      // 卡片编辑显式选「不使用账户」（accountId=null）→ 尊重 null，不被默认覆盖。
      const accountId = tx.accountId === undefined ? defaultAccountId : tx.accountId
      await bridge.insertTransaction({
        id,
        // T12-9（REQ-35）：卡内账本 chip 切换后覆盖 hook 默认账本
        bookId: overrides?.bookId ?? bookId!,
        type: tx.type,
        amountCents: tx.amountCents,
        categoryId: tx.categoryId || '',
        accountId,
        note: tx.note,
        bizDate: tx.bizDate,
        occurredAt,
        source: 'nlu_ai',
        // T12-9：卡内预算 chip 显式改过才传；未点名 → 省略，native 缺省计入
        ...(overrides?.includeInBudget !== undefined
          ? { includeInBudget: overrides.includeInBudget } : {}),
      }, newUuid()) // mutationId 必须为 UUID（服务端 sync 契约）
      written.push({ id, accountId: accountId ?? null })
    }
    return written
  }, [bridge, bookId, timezone, defaultAccountId])

  const pushBookMissingError = useCallback((proposalId?: string) => {
    const errMsg: AssistantMessage = {
      id: 'msg_' + Date.now() + '_err',
      role: 'assistant',
      text: '❌ 账本未就绪，无法记账。请返回首页重试。',
      proposalStatus: 'error',
    }
    setMessages(prev => {
      const base = proposalId
        ? prev.map(m => m.proposal?.proposalId === proposalId
            ? { ...m, proposalStatus: 'error' as const } : m)
        : prev
      return [...base, errMsg]
    })
    setActiveProposal(null)
    setCardStatus(null)
    setPhase('idle')
  }, [])

  /**
   * T18-2（REQ-67①）：**写后一次真同步**——本轮写入收尾时的唯一同步入口。
   *
   * 原先各处用 `bridge.triggerSync()`（桥命令 `sync.trigger`），它只把一轮同步
   * **入队**给 WorkManager（`SyncScheduler.enqueue`，带 `NetworkType.CONNECTED`
   * 约束）就返回；何时真跑由系统调度决定，可能延后很久、也可能被约束卡住不跑。
   * 而三个数据页（首页/明细/报表）都是**挂载期单次读**——用户从助手页切过去时，
   * 服务端刚写下的权威行还没被拉回本地 Room，页面读到的就还是旧值。现象即
   * 「AI 说改好了，聊天页与各处显示都没更新」（REQ-67 根因 H2）。
   *
   * 改用 `auth.triggerSync`：**同步阻塞**跑完一轮 push/pull（`SyncEngine.runOnce`），
   * 返回时本地库已是最新，用户随即切页就是新值。失败只吞不抛——入队语义的兜底
   * 仍在（下一轮同步自愈），页面侧另有「同步完成 / 重新可见」的重取订阅兜底。
   *
   * **不新增桥命令**：`auth.triggerSync` 是既有通道（`LoginPage` / `AiInsightCard`
   * 在用），故不触本轮桥命令计数面（决策 #1）。
   */
  const syncAfterWrite = useCallback(async () => {
    try {
      await bridge.triggerAuthSync()
    } catch {
      /* 同步失败不阻断回执：下一轮同步自愈，页面侧订阅按期重取 */
    }
  }, [bridge])

  /**
   * 尾卡（R-PROD-31）：**v2 提案的执行**——与 v1 完全不同的路径。
   *
   * v1 是「客户端本地写 + sync push」；v2 是**服务端执行**（`/ai/execute`，服务端
   * 重新校验 + 重新授权后落权威库），设备 op 例外——服务端没有它们的落点
   * （`DEVICE_EXECUTED_OPS`），由本机经既有桥命令执行。
   *
   * 回执文案直接用提案自带的 `message`：05b 起每个工具都产出一句人类可读的中文
   * （`tools.py` 里 37 处 `"message"`），它就是为这一刻写的——所以**通用卡片
   * 不需要按 op 各写一套文案**。
   */
  const commitV2Proposal = useCallback(async (
    proposal: ActionProposalV2,
  ): Promise<boolean> => {
    if (!bookId) { pushBookMissingError(proposal.proposalId); return false }
    setPhase('executing')
    setCardStatus('executing')
    try {
      const dispatch = DEVICE_OP_DISPATCH[proposal.action]
      if (dispatch) {
        await dispatch(proposal.payload ?? {}, bridge)
      } else {
        await bridge.executeProposal(
          proposal as unknown as Record<string, unknown>, bookId, newUuid(),
        )
      }
      setMessages(prev => prev.map(m =>
        m.proposal?.proposalId === proposal.proposalId
          ? { ...m, proposalStatus: 'committed' as const, text: `✅ ${proposal.message}` }
          : m
      ))
      setCardStatus('committed')
      setPhase('idle')
      await syncAfterWrite()
      return true
    } catch (err) {
      const errMsg = localizeBridgeError(err instanceof Error ? err.message : '未知错误')
      setMessages(prev => prev.map(m =>
        m.proposal?.proposalId === proposal.proposalId
          ? { ...m, proposalStatus: 'error' as const, text: `❌ 执行失败：${errMsg}` } : m
      ))
      setCardStatus('error')
      setPhase('idle')
      return false
    }
  }, [bridge, bookId, pushBookMissingError, syncAfterWrite])

  /** 首次本地提交（解析成功后自动触发，无确认按钮）。
   *  T12-9：overrides.bookId 存在时视为卡内已选账本，hook 层 bookId 缺失也可写。
   *  T-M7-03e：返回**是否真的写进去了**——确认门据此决定要不要把队列里的下一张顶上来
   *  （失败时要让 error 卡留在屏幕上给重试入口）。 */
  const commitProposalInternal = useCallback(async (
    proposal: AnyProposal, overrides?: ProposalWriteOverrides,
  ): Promise<boolean> => {
    // 尾卡（R-PROD-31）：按车道分流。v1 走下面的本地写路径（**一字未改**），
    // v2 走服务端 / 设备执行——两条路径的存储权威不同（本地 Room vs 服务端库）。
    if (isV2Proposal(proposal)) return commitV2Proposal(proposal)
    if (!bookId && !overrides?.bookId) { pushBookMissingError(proposal.proposalId); return false }
    setPhase('executing')
    setCardStatus('executing')

    try {
      const written = await writeTransactions(proposal, overrides)
      const ids = written.map(w => w.id)
      activeTxIdsRef.current = ids
      setMessages(prev => prev.map(m =>
        m.proposal?.proposalId === proposal.proposalId
          ? {
              ...m, proposal: withResolvedAccounts(proposal, written),
              proposalStatus: 'committed' as const,
              transactionIds: ids,
              text: '已记录 ' + ids.length + ' 笔交易',
            }
          : m
      ))
      setCardStatus('committed')
      setPhase('idle')

      await syncAfterWrite()
      return true
    } catch (err) {
      // 本地写入失败必须可见；卡片保持 error 态可编辑重试
      // T12-8（决策 #7）：Kotlin 英文错误消息经映射表转中文透出
      const errMsg = localizeBridgeError(err instanceof Error ? err.message : '未知错误')
      activeTxIdsRef.current = []
      setMessages(prev => prev.map(m =>
        m.proposal?.proposalId === proposal.proposalId
          ? { ...m, proposalStatus: 'error' as const, text: `❌ 记账失败：${errMsg}` } : m
      ))
      setCardStatus('error')
      setPhase('idle')
      return false
    }
  }, [bridge, bookId, pushBookMissingError, writeTransactions, syncAfterWrite])

  /** T9-8：图片批量链路等页面级流程完成后落一条普通助手消息（不进状态机）。
   *  T12-11：chat/query 气泡、操作执行回执也走本通道（提定义到 submitInput 之前以供引用）。 */
  const appendAssistantMessage = useCallback((text: string) => {
    setMessages(prev => [...prev, {
      id: 'msg_' + Date.now() + '_appended',
      role: 'assistant' as const,
      text,
      proposalStatus: 'committed' as const,
    }])
  }, [])

  /**
   * T12-11（REQ-37 ③）：chat/query 意图 → 助手文本气泡内容。
   * query 且 filter 可本地圈定 → 本地直算优先（数据权威在客户端、LLM 无数字权限，
   * 决策 #3）；无 filter/查询失败 → 转述 reply；reply 也空 → 兜底引导语。
   */
  const answerTextIntent = useCallback(async (v2: AiInterpretV2): Promise<string> => {
    const filter = v2.filter
    if (v2.intent === 'query' && filter && bookId) {
      const rows = await bridge.listTransactionsByBook(bookId)
        // T15-7：名单下传——query 直算也须尊重 type/账户/账本圈定条件
        .then(all => applyOpFilter(all, categories, filter, accounts, books))
        .catch(() => null)
      if (rows) {
        if (rows.length === 0) return '没找到符合条件的记录'
        const { count, expenseCents, incomeCents } = sumByFilter(rows)
        const yuan = (cents: number) => (cents / 100).toFixed(2)
        return `查询到 ${count} 笔记录：支出 ¥${yuan(expenseCents)}，` +
          `收入 ¥${yuan(incomeCents)}，净额 ¥${yuan(incomeCents - expenseCents)}`
      }
    }
    const reply = v2.reply
    if (v2.intent === 'query' && reply &&
        /([¥￥]\s*[\d零一二两三四五六七八九]|[\d,.]+|十?[零一二两三四五六七八九十百千万亿]+)\s*(元|块|円)/.test(reply)) {
      // T12-12：LLM 无数据权限（决策 #3），query reply 里的货币金额必为幻觉
      // （真机实测出现过「这个月总支出 358.5 元」而实账 492.5）。含金额的 reply
      // 一律不转述，引导用户给出范围走本地直算。
      return '把金额交给我从账本里直接算吧——告诉我时间或类别范围（如「这个月」「交通」）～'
    }
    // T13-6（REQ-49 / 决策 #10）：reply 空（含 bookkeeping 无金额被守卫改判 chat 的
    // 场景）→ 带当前角色名的兜底句，不再吐无角色系统腔。
    if (reply) return reply
    return persona
      ? `${persona.name}没听明白，换个说法试试？`
      : '这个问题我暂时答不上来，换个说法试试？'
  }, [bridge, bookId, categories, accounts, books, persona])

  /**
   * T15-7（REQ-56 / 决策 #8）：命名对象匹配不到时的引导语（宁缺勿错不猜测，
   * config nameHint 同款文案口径；master 总账本不可作 edit 目标，不列）。
   */
  const namedObjectHint = useCallback((kind: '账户' | '账本', name: string): string => {
    const existing = (kind === '账本'
      ? books.filter(b => !b.isMaster).map(b => b.name)
      : accounts.map(a => a.name)
    ).filter(Boolean).slice(0, 10).join('、')
    return `没找到叫「${name}」的${kind}。` +
      (existing ? `现有的：${existing}。` : `目前还没有可用的${kind}，请先在设置中创建。`)
  }, [books, accounts])

  /**
   * T12-11（REQ-37 ③ / 决策 #5）：edit/delete 意图 → 当前账本本地圈定候选，
   * 交 OpPreviewCard 预览确认（破坏性操作必过确认）；守卫态与零命中以气泡收口。
   * T15-7（REQ-56 / 决策 #8）：filter/set 点名对象（accountName/bookName）先过
   * 名→id 解析——filter 侧指代不明直接引导（绝不回落当前账本盲猜目标笔）；
   * set 侧匹配不到 → 该字段不回写（从 set 剔除 + 引导语，其余字段照常预览）。
   */
  const openOpPreview = useCallback(async (v2: AiInterpretV2) => {
    const filter = v2.filter
    if (v2.intent === 'edit' && !v2.set) {
      appendAssistantMessage('想改哪些记录的什么内容呢？说说范围（日期/分类/关键词）和新值～')
      return
    }
    if (!filter || !hasOpConditions(filter)) {
      appendAssistantMessage(v2.intent === 'delete'
        ? '请说明要删除哪些记录（如日期、分类或关键词）——我不做无范围的整账本删除'
        : '请说明要修改哪些记录（如日期、分类或关键词）')
      return
    }
    if (!bookId) {
      appendAssistantMessage('❌ 账本未就绪，无法查找要处理的记录。请返回首页切换账本后重试。')
      return
    }
    // filter 侧点名：命中他账 → 跨账本取数（改「别的账本里的记录」场景）
    let listBookId = bookId
    if (filter.bookName) {
      const hit = matchBookByName(filter.bookName, books)
      if (!hit) {
        appendAssistantMessage(namedObjectHint('账本', filter.bookName))
        return
      }
      listBookId = hit.id
    }
    if (filter.accountName && !matchAccountByName(filter.accountName, accounts)) {
      appendAssistantMessage(namedObjectHint('账户', filter.accountName))
      return
    }
    // set 侧点名（仅 edit）：解析不到的字段剔除 + 引导语；剔空 → 不开卡
    let set = v2.set
    if (v2.intent === 'edit' && set) {
      const notes: string[] = []
      if (set.accountName && !matchAccountByName(set.accountName, accounts)) {
        notes.push(`${namedObjectHint('账户', set.accountName)}该项未改。`)
        set = { ...set, accountName: undefined }
      }
      if (set.bookName && !matchBookByName(set.bookName, books)) {
        notes.push(`${namedObjectHint('账本', set.bookName)}该项未改。`)
        set = { ...set, bookName: undefined }
      }
      if (notes.length > 0) {
        appendAssistantMessage(notes.join(''))
        const hasRealChange = Object.values(set).some(v => v !== undefined)
        if (!hasRealChange) return
      }
    }
    const all = await bridge.listTransactionsByBook(listBookId).catch(() => null)
    if (!all) {
      appendAssistantMessage('查找本地账单失败，请稍后重试')
      return
    }
    const rows = applyOpFilter(all, categories, filter, accounts, books)
    if (rows.length === 0) {
      appendAssistantMessage('没找到符合条件的记录')
      return
    }
    // 调用点已限定 intent ∈ {edit, delete}，kind 显式收窄
    const kind: 'edit' | 'delete' = v2.intent === 'delete' ? 'delete' : 'edit'
    setPendingOp({ kind, reply: v2.reply, set, candidates: rows })
  }, [
    appendAssistantMessage, bridge, bookId, categories,
    books, accounts, namedObjectHint,
  ])

  /**
   * T13-5（REQ-48 / 决策 #6 #8）：config 意图 → 名→id 本地解析 + 预览确认。
   * 坏形状（intent=config 但 config 缺失/op 不在白名单）→ 回落 chat 转述 reply
   * （决策 #6）；解析失败（匹配不到/重名 create/期限过期）→ 引导语气泡，不开卡；
   * 解析成功 → pendingConfig（确认前零写入）。
   */
  const openConfigPreview = useCallback(async (v2: AiInterpretV2) => {
    if (!v2.config) {
      appendAssistantMessage(await answerTextIntent(v2))
      return
    }
    const today = todayBizDate(timezone)
    const r = resolveConfigTargets(v2.config, {
      books, accounts, goals, categories, bookId, today,
    })
    if (!r.ok) {
      appendAssistantMessage(r.message)
      return
    }
    setPendingConfig({
      cfg: v2.config, reply: v2.reply, targets: r.targets,
      preview: buildConfigPreview(v2.config, today),
    })
  }, [
    appendAssistantMessage, answerTextIntent, timezone,
    books, accounts, goals, categories, bookId,
  ])

  /**
   * T-M7-02i / T-M7-03b：一轮 agent 流的收尾——把投影里的产物落到既有链路上。
   *
   * 收尾由 `useAssistantStream` 在 `done` / `error` / 取消三种情形下各调一次，载荷是
   * **最终投影**（含正文与推理增量、步骤、`done` / `error` / `canceled` 标志）。
   *
   * 失败（`error` 帧或桥层异常）与取消：**保留已渲染的半截文本并标注，不清空**
   * （架构 §8.5——内容突然消失比「看到一半 + 一句失败提示」更糟）。这两轮服务端都不
   * 落库（02f 的一轮原子写入），所以半截文本只活在本地消息里，重开会话即不在；
   * 断连恢复归 P3。**两者分开**：取消是用户主动、断连是被动，UI 表现与恢复路径不同
   * （03e 的边界），故不合并成一个状态。
   *
   * 成功轮的落点按 `done` 的产物三选一，顺序即优先级：
   *   ① `executions` 非空 → 服务端已执行（单 Step 高置信），落回执 + 记事务 id 供撤销；
   *   ② 挂起提案队列非空 → 挂起的提案（多步写入强制确认，T-M7-02e 决策 3），
   *      进 `pending-confirm` 卡态等用户点头——**不自动写库**；
   *   ③ 都没有 → 纯文本答复（chat / query），气泡定型成正式消息。
   * `proposals` 与 `executions` 互斥（T-M7-02e 决策 2），所以①②不会同时命中。
   *
   * **T-M7-03e 起确认门为事件驱动 + 权威核对：** 卡在 `confirm` 帧到达时就出现
   * （见 `handleApprovalEvent`），但**只有 `done.proposals`（权威清单）里列出的提案
   * 才可被确认**——队列里不在清单上的（中途来的、或权威清单没有它的）一律按
   * `unavailable` 收敛：**视为拒绝、零写**（架构 §7.4「不确定时倾向拒绝」）。
   * 这既保住了 02i 决策 ③「不给中途提案开写入口」，又让用户能提前看见 agent 的意图。
   *
   * **不假设「每条回复都从 `delta` 开始」**（02g-2 探针：快档直调工具那一步 0 帧
   * `delta`）——`text` 为空时三个落点各自有兜底文案，不是错误路径。
   */
  const settleStream = useCallback(async (p: AgentStreamProjection) => {
    finishProcessing()
    // T-M7-05f：流收尾了，等用户开口的那条设备语音请求随之作废——服务端那一轮已经
    // 结束，挂起条目要么已超时收敛、要么随 Turn 注销，回传不再有意义（真回传也是 404，
    // 那条路径按「已收敛」吞掉）。提示条要收掉，否则用户会对着一条不再有用的提示说话。
    deviceVoiceRef.current = null
    setDeviceVoiceRequest(null)
    const text = p.text
    // T-M7-04e：游标要落在**活动桶的键**上。成功路径的第 1 轮里这个值还是 null
    // （要等下面的 `adoptConversation` 才变成服务端 id），所以那里不用它、用
    // `done.conversation_id`；失败与取消两条路径的活动键就是会话本身的键。
    const activeKey = useAssistantChatStore.getState().activeConversationId

    if (p.canceled) {
      // T-M7-03e：审批四值的 `cancelled`——用户主动中止整个任务。挂着的确认卡是
      // 「用户没来得及回答」的提案，按下停止就不该再留一张卡等他（架构 §4.4
      // 不变式 3：取消必须收敛到一致状态，不留悬挂的确认卡）。**零写。**
      dropApprovals(() => true)
      setInterrupted(null)
      // T-M7-04e：**取消把游标抹成未知**——服务端在断连取消时**不写任何日志事件**
      // （`CancelledError` 不被 `except Exception` 捕获），但这一轮**已经落库的部分**
      // 确实在游标之后；而本机这半截气泡是流的拼接、反推不出它对应到日志的哪一行。
      // 推进会漏、不推进会重，唯一安全的选择是**放弃这条会话的增量**——下一次正常
      // 收尾会把游标重新写上（自愈）。理由详见 store 的 `forgetCursor`。
      if (activeKey !== null) forgetCursor(activeKey)
      appendAssistantMessage(text ? `${text}\n\n已取消` : '已取消')
      return
    }

    const done = p.done
    if (!done) {
      // T-M7-03d：**这一轮带的 conversation_id 已失效**（服务端在开流之前回 404，
      // 桥把它译成 `not_found` 帧）。终态是 03b 交接的降级契约：丢弃这个 id，
      // 下一轮不带它 → 服务端开新会话。当前这一屏消息**不动**（forgetConversation
      // 的语义），用户重发一次即可继续，不需要去别处找会话。
      if (p.error?.code === 'not_found') {
        dropApprovals(() => true)
        setInterrupted(null)
        forgetConversation()
        appendAssistantMessage(text
          ? `${text}\n\n⚠️ 这条会话已失效（可能已在其它设备删除），已切回本地会话，重发即可继续`
          : '⚠️ 这条会话已失效（可能已在其它设备删除），已切回本地会话，重发即可继续')
        return
      }
      // T-M7-03e：失败 / 断连是**被动**中断（与用户主动取消是两回事，故不合并）。
      // ① 没被回答的挂起提案按 `unavailable` 收敛——**视为拒绝**，零写；
      // ② 留一个重连入口（R-PROD-19：弱网 / 后台断连的提示与恢复起点）。
      // T-M7-04b：先补记服务端日志里**已经落库**的部分——服务端自本卡起逐事件落库，
      // 断连不再等于「这一轮什么都没留下」；补得到的正文（`recovered`）按到达顺序
      // 落在中断提示**之前**，读起来就是「做到哪 → 然后断了」。空数组是常态
      // （本地半截气泡已经包含那些正文，见 `recoveredMessages` 的规则 ③）。
      for (const item of p.recovered) appendAssistantMessage(item.text)
      const dropped = dropApprovals(() => true)
      setInterrupted({ input: lastTurnInputRef.current })
      const note = dropped > 0 ? '；未确认的改动没有记录' : ''
      // T-M7-04e：失败轮的日志位置也要落盘。续读（`catchUp`）刚把游标推到了服务端日志
      // 的真实位置，`logCursor` 带的就是它——不写的话，这一轮留在日志里的 `user` 事件
      // 会在下一次增量补齐时**重复显示一次**。取的是**常量**：同一件事只有一份文案
      // （重建侧用同一个，`history-projection.ts`）。
      if (activeKey !== null && p.logCursor > 0) setCursor(activeKey, p.logCursor)
      appendAssistantMessage(text
        ? `${text}\n\n${INTERRUPTED_TURN_NOTE}${note}`
        : `${INTERRUPTED_TURN_NOTE}${note}`)
      return
    }

    // 会话 id 固化进 store：下一轮必须带上，否则每轮都开新会话（02f 的会话索引靠它接续）
    adoptConversation(done.conversation_id)
    // T-M7-04e：本轮结束的日志位置落盘（跨设备增量补齐的起点）。用收尾时的 `logCursor`
    // 而不是 `done.seq`——两者在这里恒等，取同一个来源少一处口径。
    if (p.logCursor > 0) setCursor(done.conversation_id, p.logCursor)
    setInterrupted(null)

    // T-M7-03e：`done.proposals` 是**权威清单**（`stream.py` 口径），用它核对队列——
    // ① 不在清单上的（中途来的 / 权威清单没有它的）→ `unavailable`，视为拒绝、零写；
    // ② 清单里但队列里没有的（`confirm` 帧掉了）→ 补进队列，不因丢帧而漏掉一个提案。
    const authoritative = new Set<string>()
    for (const pr of done.proposals) {
      const id = (pr as { proposalId?: unknown }).proposalId
      if (typeof id === 'string') authoritative.add(id)
    }
    const stranded = dropApprovals(a => !authoritative.has(a.proposal.proposalId))
    if (stranded > 0) {
      appendAssistantMessage(`⚠️ 有 ${stranded} 项改动未取得确认，没有记录`)
    }
    for (const pr of done.proposals) {
      enqueueApproval(pr as unknown as ActionProposal, null)
    }

    if (done.executions.length > 0) {
      const ids = done.executions.flatMap(e => (e.transaction_ids as string[] | undefined) ?? [])
      activeTxIdsRef.current = ids
      // 回执消息带上事务 id：撤销后消息要能翻成「已撤销」（undoExecution 按它匹配）。
      // 已执行那笔拿不到提案正文（服务端只在**挂起**时发 confirm 帧，自动执行走
      // tool_result 摘要），所以这里不渲染 ConfirmationCard——撤销入口因此只有
      // activeTxIdsRef 这条内存态通道（切页即失效，与 T16-2 的历史卡同口径）。
      setMessages(prev => [...prev, {
        id: 'msg_' + Date.now() + '_assistant',
        role: 'assistant' as const,
        text: `${text ? text + '\n\n' : ''}已记录 ${ids.length} 笔交易`,
        proposalStatus: 'committed' as const,
        transactionIds: ids,
      }])
      await syncAfterWrite()
      return
    }

    const head = approvalsRef.current[0]
    if (head) {
      // 挂起的提案 requiresConfirmation 恒为 True（T-M7-02e 决策 2），一律过确认门；
      // 用户点卡内「确认」后由 editProposal 的待确认分支接管首次写入。
      // 消息流的落点交给 `showPendingApproval`（建消息 / 用正文替换兜底文案），
      // 它同时管「队列里第二张顶上时也要有一条消息」——回执与历史卡都挂在消息上。
      showPendingApproval(text)
      return
    }

    appendAssistantMessage(text || (done.stop_reason === 'empty_response'
      ? '这次没想出合适的回答，换个说法再试试？'
      : '本轮没能得出结论，换个说法再试试？'))
  }, [
    adoptConversation, appendAssistantMessage, bridge, dropApprovals, enqueueApproval,
    finishProcessing, forgetConversation, forgetCursor, setCursor, setMessages,
    showPendingApproval, syncAfterWrite,
  ])

  /**
   * T-M7-03e：`confirm` 帧到达 → 建确认卡（**事件驱动**的那一半）。
   *
   * P1 是「等 `done` 之后按产物分发渲染」（用户看到的是「思考完毕展开卡」）；03e 起卡
   * 随事件实时出现，用户能在 agent 还在跑的时候就看见它想做什么（架构 §7.4：审批卡
   * 同时是安全门与人机纠偏点）。**但动作在收尾后才启用**——`done.proposals` 才是权威
   * 清单，中途来的提案在收尾核对时若不在清单上，按 `unavailable` 收敛（见 settleStream）。
   */
  const handleApprovalEvent = useCallback((
    raw: Record<string, unknown>, step: number | null,
  ) => {
    if (!enqueueApproval(raw as unknown as ActionProposal, step)) return
    showPendingApproval()
  }, [enqueueApproval, showPendingApproval])

  /**
   * T-M7-05f：把一次设备语音请求交给界面（见 `deviceVoiceRequest` 的说明）。
   *
   * **结算路径有两条，且都必须走到**：用户说完 → `respondDeviceVoice`；用户取消 / 录音
   * 失败 → `dismissDeviceVoice`。服务端那一轮正挂在这一句上等，不回传它就白等满
   * `ai_device_timeout_seconds`（超时是它的收敛路径，但那是用户已经看得到的等待）。
   */
  const clearDeviceVoice = useCallback(() => {
    deviceVoiceRef.current = null
    setDeviceVoiceRequest(null)
  }, [])

  /**
   * T-M7-05f：`device_request` 帧到达（`useAssistantStream` 实时转发）→ 执行。
   *
   * 语音以外的能力一并在这里委托给执行器（它调 `device.execute`，需结果类顺带回传）；
   * 语音只**置位**，等界面走完既有录音路径（见 `deviceVoiceRequest`）。
   */
  const handleDeviceRequest = useCallback((frame: DeviceRequestFrame) => {
    if (frame.capability !== VOICE_CAPABILITY) {
      void runDeviceRequest(bridge as DeviceExecutorBridge, frame)
      return
    }
    if (deviceVoiceRef.current) {
      // 同一时刻只等一次语音输入：人的动作没法排队，而两条同时挂起只会让先到的那条
      // 永远等不到回传。后来者按 `unavailable` 当场结算（模型拿到既定的「此刻用不了」
      // 文案，不悬挂）。
      void deliverDeviceResult(bridge as DeviceExecutorBridge, frame.request_id, 'unavailable')
      return
    }
    deviceVoiceRef.current = frame
    setDeviceVoiceRequest(frame)
  }, [bridge])

  /** T-M7-05f：用户说完了——把转写文本回传，唤醒挂起的那一轮 loop。 */
  const respondDeviceVoice = useCallback((text: string) => {
    const frame = deviceVoiceRef.current
    if (!frame) return
    clearDeviceVoice()
    void deliverDeviceResult(bridge as DeviceExecutorBridge, frame.request_id, 'ok', { text })
  }, [bridge, clearDeviceVoice])

  /** T-M7-05f：这次语音输入没成（用户取消 / 录音失败）——按结局回传同样不能省。 */
  const dismissDeviceVoice = useCallback((status: 'denied' | 'failed' = 'denied') => {
    const frame = deviceVoiceRef.current
    if (!frame) return
    clearDeviceVoice()
    void deliverDeviceResult(bridge as DeviceExecutorBridge, frame.request_id, status)
  }, [bridge, clearDeviceVoice])

  /**
   * T-M7-02i / T-M7-03b：agent 流的收发（订阅、`streamId` 归属、收尾幂等、取消）
   * 在 `useAssistantStream` 里，本 hook 只提供收尾回调。
   *
   * 流的中间态**不做 intent 判定**——v2 intent 在流式中无法预知（chat/query/edit/
   * delete/config 的 UI 形态互异），只有 `done` 的产物决定落点（架构 §9.1：复杂度对
   * 用户不可见）。
   */
  const { projection, start: startAgentStream, cancel: cancelAgentStream } =
    useAssistantStream({
      bridge, onSettled: settleStream, onConfirm: handleApprovalEvent,
      onDeviceRequest: handleDeviceRequest,
    })

  /**
   * T-M7-03b：起一轮 agent 流（**开关开启且桥具备能力时才走到这里**——闸在
   * `submitInput` 里，与 02i 逐字相同）。
   *
   * 会话 id 从 store 现读（不再是内存 ref）：`adoptConversation` 会在上一轮 `done`
   * 时把它固化，切页 remount 也不丢。
   */
  const runAgentTurn = useCallback(async (text: string) => {
    // T-M7-03e：留一份本轮输入——本轮若被中断，重连入口凭它原样重发
    lastTurnInputRef.current = text
    const conversationId = useAssistantChatStore.getState().activeConversationId
    await startAgentStream(text, bookId!, conversationId)
  }, [bookId, startAgentStream])

  /**
   * T-M7-03b：取消当前 agent 流（`ai.cancel` 接线；03e 的「停止生成」按钮接它）。
   *
   * **零写**——只关 SSE 长连接与收敛本地态，不触碰任何写命令。三个互斥瞬时态
   * （`activeProposal` / `pendingOp` / `pendingConfig`）一并收敛为空：T12-11 的
   * 「新输入接管会话、未决卡作废」口径在「取消」这一侧同样成立——用户按下停止，
   * 就不该再留一张待确认的卡等着他。
   */
  const cancelStream = useCallback(async () => {
    // T-M7-03e：未决的确认门一并收敛（审批四值的 `cancelled`）——用户按下停止，
    // 就不该再留一张待确认的卡等着他（架构 §4.4 不变式 3）。
    dropApprovals(() => true)
    setInterrupted(null)
    setActiveProposal(null)
    setCardStatus(null)
    setPendingOp(null)
    setPendingConfig(null)
    isPendingCategoryRef.current = false
    isPendingConfirmRef.current = false
    shownApprovalIdRef.current = null
    // 撤销窗（activeTxIdsRef）不动：那是**上一轮已落库**那笔的入口，与取消本轮无关
    await cancelAgentStream()
  }, [cancelAgentStream, dropApprovals])

  /**
   * T-M7-03e：确认门上的「拒绝」（审批四值 `rejected`）——**零写**，任务不中止。
   *
   * 与「停止生成」（`cancelled`）分开：拒绝是「这一项我不认可，别的接着来」，
   * 取消是「整个任务到此为止」。两者都零写，但文案与后续不同——合并成一个状态
   * 会让用户分不清「agent 还会继续」还是「已经结束了」。
   */
  const rejectApproval = useCallback(() => {
    const shown = shownApprovalIdRef.current
    if (!shown) return
    dropApprovals(a => a.proposal.proposalId === shown)
    appendAssistantMessage('已拒绝这次改动，没有记录')
    showPendingApproval() // 队列里还有下一张就顶上（没有则卡保持在撤下的状态）
  }, [appendAssistantMessage, dropApprovals, showPendingApproval])

  /** T-M7-03b：气泡视图——页面渲染读它（03c 的步骤卡片流读 `projection` 的 `steps`）。 */
  const streaming = useMemo<StreamingBubble | null>(
    () => (projection ? { text: projection.text, reasoning: projection.reasoning } : null),
    [projection],
  )

  /** T-M7-03e：本轮是否仍在流式中（投影在场即未收尾）。确认门上的**动作只在收尾后
   *  启用**——第一道在 UI（按钮禁用，卡上写明「本轮仍在执行」），第二道在这里。 */
  const streamActive = projection !== null

  const submitInput = useCallback(async (text: string) => {
    if (!text.trim()) return
    // C-B3 fix: 并发提交互斥 — 非 idle 阶段拒绝新提交
    if (isSubmittingRef.current) return
    isSubmittingRef.current = true

    // T12-11：新输入接管会话，未决操作卡作废（不静默执行）
    // T13-5：config 预览卡同理
    setPendingOp(null)
    setPendingConfig(null)
    // T-M7-03e：挂着的确认门与中断提示同理——新一轮是新的任务，上一轮未回答的挂起
    // 提案随之作废（**零写**：它们本来就没落库，作废不丢任何东西）
    dropApprovals(() => true)
    setInterrupted(null)

    const userMsg: AssistantMessage = {
      id: 'msg_' + Date.now() + '_user',
      role: 'user',
      text: text.trim(),
    }
    setMessages(prev => [...prev, userMsg])
    setPhase('processing')
    setIsSlow(false)

    // 12s 慢速提示
    if (slowTimerRef.current) clearTimeout(slowTimerRef.current)
    slowTimerRef.current = setTimeout(() => setIsSlow(true), SLOW_THRESHOLD_MS)

    // 1. 本地 NLU
    const parseCtx = buildParseContext()
    const localProposal = parseInput(text.trim(), parseCtx)

    // T12-12（真机收口发现）：本地 NLU 会把操作/查询句里的金额当新账解析
    // （「把昨天的午饭改成15块」被记成新的 ¥15）。命中操作句式必须送云端
    // intent v2；云端不可达时按 REQ-37 ④ canned，绝不以本地结果硬记。
    const looksLikeOp = OP_SENTENCE_RE.test(text)
    const needsAI = needsAiRouting(text, localProposal)

    let finalProposal: ActionProposal = localProposal

    if (looksLikeOp && !(isOnline() && bridge.isAvailable())) {
      appendAssistantMessage(AI_OFFLINE_CANNED)
      finishProcessing()
      return
    }

    // 2. 尝试 AI —— T12-11（REQ-37 / 决策 #2）：needsAI 判据不翻向；
    //    v2 输出按 intent 分发（bookkeeping 现链路 / chat+query 文本气泡 /
    //    edit+delete 预览确认卡）。aiConsumed=意图已被非提案链路消化，直接收尾。
    if (needsAI && isOnline() && bridge.isAvailable()) {
      // T-M7-02i：agent 流式通道优先（架构 §11.3 的双层灰度：服务端开关 + 客户端开关）。
      // **两道闸都要过**：开关开 **且** 桥具备流式能力（02h 未落地、或旧 APK 的命令表里
      // 没有这两条时，能力探测为假）。任一不满足即整条走下面的既有单发链路，行为与
      // 02i 之前逐字相同——这也顺带兜住「JS 与 APK 版本不同步」的部署形态。
      const agentBridge = bridge as unknown as Partial<AgentStreamBridge>
      const canStream = typeof agentBridge.runAgent === 'function' &&
        typeof agentBridge.onAiStream === 'function'
      if ((agentStream ?? AI_AGENT_CLIENT_ENABLED) && canStream) {
        // 收尾由 settleStream 负责（含 finishProcessing），这里直接交棒
        await runAgentTurn(text.trim())
        return
      }
      // T11-6/T11-7（决策 #7）：画像/人设任一非空才拼对应段。
      // T12-11（REQ-37）：today 与 categories 常驻注入——相对日期换算与分类白名单
      // 是 prompt v2 全部 intent 的公共前提（桥侧 context 整包透传，服务端缺段零影响）。
      // T13-5（REQ-48 / 决策 #7）：books/accounts/budgets/goals 现有对象名单段——
      // config 意图点名与避重名的依据；空列表 → null 段（服务端渲染跳过）。
      const hasProfile = !!profile && Object.keys(profile).length > 0
      const objNames = (names: string[]): AiInterpretContext['books'] => {
        const seg = buildAiObjectNames(names)
        return seg.length ? seg : null
      }
      const aiContext: AiInterpretContext = {
        profile: hasProfile ? profile : null,
        persona: persona ? { name: persona.name, text: persona.text } : null,
        categories: aiCategories?.length ? aiCategories : null,
        today: todayBizDate(timezone),
        books: objNames(books.map(b => b.name)),
        accounts: objNames(accounts.map(a => a.name)),
        budgets: objNames(budgets.map(b => b.categoryId
          ? (categories.find(c => c.id === b.categoryId)?.name ?? '')
          : '总预算')),
        goals: objNames(goals.map(g => g.name)),
      }
      try {
        const aiResult = await bridge.interpretAI(text.trim(), aiContext)
        if (aiResult?.output) {
          const v2 = parseAiInterpretOutput(aiResult.output)
          if (v2.intent === 'chat' || v2.intent === 'query') {
            appendAssistantMessage(await answerTextIntent(v2))
            finishProcessing()
            return
          }
          if (v2.intent === 'edit' || v2.intent === 'delete') {
            await openOpPreview(v2)
            finishProcessing()
            return
          }
          // T13-5（REQ-48）：config → 名→id 解析 + 预览确认（确认前零写入）
          if (v2.intent === 'config') {
            await openConfigPreview(v2)
            finishProcessing()
            return
          }
          // bookkeeping（含 intent 缺失的老输出）→ 现链路
          finalProposal = buildProposalFromAiOutput(v2.legacy, {
            now: Date.now(),
            timezone,
            categories,
            bookId: bookId || '',
          })
        } else if (looksLikeOp) {
          // 桥返回空 output：操作句同样不回落本地（T12-12）
          appendAssistantMessage(AI_OFFLINE_CANNED)
          finishProcessing()
          return
        }
      } catch (err) {
        // T12-8（REQ-30 ② / 决策 #9）：云端失败不再静默——会话级一次性轻提示后回退本地
        if (!aiNoticeShownRef.current) {
          aiNoticeShownRef.current = true
          const code = typeof err === 'object' && err !== null && 'code' in err
            ? String((err as { code: unknown }).code) : ''
            const desc = AI_INTERPRET_ERROR_ZH[code] ?? '云端 AI 暂时不可用'
          // T12-12：操作句不回落本地（否则「改X」被记成「新账X」），提示语区分
          const suffix = looksLikeOp ? '操作与查询需要云端可用，本次未改动账本' : '本次以本地解析继续'
          setMessages(prev => [...prev, {
            id: 'msg_' + Date.now() + '_ai_notice',
            role: 'assistant' as const,
            text: `⚠️ ${desc}，${suffix}`,
            proposalStatus: 'committed' as const,
          }])
        }
        if (looksLikeOp) {
          appendAssistantMessage(AI_OFFLINE_CANNED)
          finishProcessing()
          return
        }
        finalProposal = localProposal
      }
    }

    // 3. 服务端验证（仅对 AI 生成的交易提案；best-effort，失败不阻断本地写）
    if (finalProposal !== localProposal &&
        (finalProposal.action === 'add_tx' || finalProposal.action === 'add_tx_batch') && bookId) {
      try {
        const validation = await bridge.validateProposal(
          finalProposal as unknown as Record<string, unknown>, bookId
        )
        if (!validation.valid && validation.verdict !== 'rejected_low_confidence') {
          finalProposal = localProposal
        }
      } catch {
        // 验证请求失败 → 保留 AI 结果
      }
    }

    // 4. 清理慢速提示
    if (slowTimerRef.current) {
      clearTimeout(slowTimerRef.current)
      slowTimerRef.current = null
    }
    setIsSlow(false)

    const isTxAction = finalProposal.action === 'add_tx' || finalProposal.action === 'add_tx_batch'
    // T12-8（REQ-30 ① / 决策 #7）：缺分类的提案不自动硬写（Kotlin require 会抛英文错），
    // 卡片转「待选分类」态——选后才落库；Kotlin require 保留作最后防线（错误已中文化）。
    const missingCategory = isTxAction &&
      (finalProposal.transactions ?? []).some(t => !t.categoryId)
    // T12-11（REQ-37 ④）：AI 未消化（离线/无 key/失败）且本地 NLU 也只落到 chat
    // 兜底 → 用户想聊/查/改/删但云端不可达，统一 canned 文案（bookkeeping 保持本地 NLU）。
    const opCanned = !isTxAction && finalProposal === localProposal &&
      localProposal.action === 'chat'
    const assistantMsg: AssistantMessage = {
      id: 'msg_' + Date.now() + '_assistant',
      role: 'assistant',
      text: missingCategory
        ? '未识别到分类，请在卡片中选择分类后保存（尚未记账）'
        : opCanned ? AI_OFFLINE_CANNED : finalProposal.message,
      proposal: finalProposal,
      proposalStatus: missingCategory ? 'pending' : isTxAction ? 'executing' : 'committed',
    }
    setMessages(prev => [...prev, assistantMsg])

    if (isTxAction) {
      // 第八轮 Bug-6：免确认——解析成功直接本地写入，卡片常驻可编辑
      setActiveProposal(finalProposal)
      isSubmittingRef.current = false
      isPendingCategoryRef.current = missingCategory
      if (missingCategory) {
        setCardStatus('pending-category')
        setPhase('idle')
        return
      }
      await commitProposalInternal(finalProposal)
      return
    }
    setPhase('idle')
    isSubmittingRef.current = false
  }, [
    bridge, categories, bookId, timezone, profile, persona, aiCategories,
    books, accounts, budgets, goals, agentStream,
    buildParseContext, commitProposalInternal, dropApprovals, finishProcessing, runAgentTurn,
    appendAssistantMessage, answerTextIntent, openOpPreview, openConfigPreview,
  ])

  /** 卡片编辑保存：替换写（softDelete 旧 + insert 新），不新增 transaction.update。
   *  T12-9（REQ-35）：overrides 透传给首提与替换写（卡内切账本 = 该笔改挂新账本）。 */
  const editProposal = useCallback(async (
    edited: AnyProposal, overrides?: ProposalWriteOverrides,
  ) => {
    if (!bookId && !overrides?.bookId) { pushBookMissingError(edited.proposalId); return }
    // T12-8（REQ-30 ①）：待选分类态——分类补齐前编辑只回写消息（不写库、态保持），
    // 选定分类后接管首次写入（commitProposalInternal：文案「已记录 N 笔」+ syncAfterWrite）
    if (isPendingCategoryRef.current) {
      // 尾卡（R-PROD-31）：v2 没有「待选分类」这回事——它的 payload 形状由服务端
      // 工具注册表拥有，补分类是 v1 本地解析路径的产物。这条守卫同时给类型收窄。
      if (isV2Proposal(edited)) return
      setActiveProposal(edited)
      setMessages(prev => prev.map(m =>
        m.proposal?.proposalId === edited.proposalId ? { ...m, proposal: edited } : m
      ))
      if ((edited.transactions ?? []).some(t => !t.categoryId)) return
      isPendingCategoryRef.current = false
      await commitProposalInternal(edited, overrides)
      return
    }
    // T-M7-02i / T-M7-03e：待确认态（agent 挂起的提案）——用户点了卡内「确认」即接管首次写入。
    // 与上一段同构，差别只在触发条件：这里不要求补齐任何字段，因为服务端已经组装并
    // 校验过提案，用户点头就是唯一前置（T-M7-02e 决策 3：多步写入一律强制确认）。
    // 这就是审批四值的 `allowed-once`——**唯一放行值**，且执行后即失效（架构 §7.4：
    // 不产生长期授权，下一次挂起会重新问一遍）。
    if (isPendingConfirmRef.current) {
      // T-M7-03e：**动作只在收尾后启用**——`done.proposals` 才是权威清单，中途来的
      // 提案尚未被服务端最终确认（UI 上的按钮此时是禁用的，这里是第二道防线）。
      if (streamActive) {
        appendAssistantMessage('⚠️ 本轮还在执行，稍候再确认这次改动')
        return
      }
      dropApprovals(a => a.proposal.proposalId === edited.proposalId)
      setActiveProposal(edited)
      setMessages(prev => prev.map(m =>
        m.proposal?.proposalId === edited.proposalId ? { ...m, proposal: edited } : m
      ))
      const written = await commitProposalInternal(edited, overrides)
      // 写入成功且队列里还有下一张 → 顶上（多步任务一轮可以挂起多个写提案）。
      // 失败时**不顶**：error 卡要留在屏幕上给重试入口。
      if (written) showPendingApproval()
      return
    }
    // 尾卡（R-PROD-31）：走到这里只剩 v1——v2 提案只可能来自确认门（服务端挂起的
    // 那一张），上面那个分支已经 return 了。这条守卫既是给类型收窄，也是**下一道
    // 防线**：万一将来有别的路径把 v2 送进来，它不会掉进下面的 v1 写路径
    // （那条路径读 `transactions`，对 v2 读到的是 undefined，会静默什么都不写）。
    if (isV2Proposal(edited)) return
    const oldIds = activeTxIdsRef.current
    setPhase('executing')
    setCardStatus('executing')
    try {
      for (const id of oldIds) {
        await bridge.softDeleteTransaction(id, newUuid())
      }
      const written = await writeTransactions(edited, overrides)
      const ids = written.map(w => w.id)
      activeTxIdsRef.current = ids
      setMessages(prev => prev.map(m =>
        m.proposal?.proposalId === edited.proposalId
          ? {
              ...m, proposal: withResolvedAccounts(edited, written),
              proposalStatus: 'committed' as const,
              transactionIds: ids,
              text: '已更新记录',
            }
          : m
      ))
      setCardStatus('committed')
      setPhase('idle')
      await syncAfterWrite()
    } catch (err) {
      const errMsg = localizeBridgeError(err instanceof Error ? err.message : '未知错误')
      setMessages(prev => prev.map(m =>
        m.proposal?.proposalId === edited.proposalId
          ? { ...m, proposalStatus: 'error' as const, text: `❌ 保存失败：${errMsg}` } : m
      ))
      setCardStatus('error')
      setPhase('idle')
    }
  }, [bridge, bookId, pushBookMissingError, writeTransactions, commitProposalInternal,
      dropApprovals, showPendingApproval, streamActive, appendAssistantMessage, syncAfterWrite])

  const undoExecution = useCallback(async () => {
    const ids = activeTxIdsRef.current
    if (ids.length === 0) { setPhase('idle'); return }
    try {
      for (const txId of ids) {
        await bridge.softDeleteTransaction(txId, newUuid())
      }
      setMessages(prev => prev.map(m =>
        m.transactionIds && m.transactionIds.some(id => ids.includes(id))
          ? { ...m, proposalStatus: 'undone' as const, text: '已撤销' } : m
      ))
      activeTxIdsRef.current = []
      setActiveProposal(null)
      setCardStatus(null)
      await syncAfterWrite()
    } catch {
      // C-B1 fix: undo 失败时通知用户，不静默吞掉
      setMessages(prev => prev.map(m =>
        m.transactionIds && m.transactionIds.some(id => ids.includes(id))
          ? { ...m, proposalStatus: 'error' as const, text: '撤销失败，请手动处理' } : m
      ))
    }
    setPhase('idle')
  }, [bridge, syncAfterWrite])

  /**
   * T12-11（REQ-37 ③ / 决策 #3 #5）：执行 pendingOp——逐笔 updateTransaction/
   * softDeleteTransaction（各自 mutationId UUID，上限 MAX_BATCH_OPS）。
   * 逐笔独立、部分失败可见不回滚（与手动记账「逐笔入队、失败可见」同口径）；
   * 成功回执优先用云端 reply（仅全成功无截断时），否则模板语；有写入才 syncAfterWrite。
   */
  const confirmOp = useCallback(async () => {
    const op = pendingOp
    if (!op || opBusyRef.current) return
    opBusyRef.current = true
    const rows = op.candidates.slice(0, MAX_BATCH_OPS)
    const now = Date.now()
    let done = 0
    let firstError = ''
    for (const tx of rows) {
      try {
        if (op.kind === 'delete') {
          await bridge.softDeleteTransaction(tx.id, newUuid())
        } else {
          // T15-7（REQ-56）：accounts/books 名单下传——set 的账户/账本名→id 解析
          const input = buildEditUpdates(tx, op.set, categories, timezone, now, accounts, books)
          if (!input) throw new Error('该笔缺少分类或数据异常，无法修改')
          await bridge.updateTransaction(input, newUuid())
        }
        done++
      } catch (err) {
        if (!firstError) {
          firstError = localizeBridgeError(err instanceof Error ? err.message : '未知错误')
        }
      }
    }
    setPendingOp(null)
    opBusyRef.current = false
    const verb = op.kind === 'delete' ? '删除' : '更新'
    const failed = rows.length - done
    const skipped = op.candidates.length - rows.length
    let text: string
    if (done === 0) {
      text = `❌ ${verb}失败：${firstError || '未知错误'}`
    } else if (op.reply && failed === 0 && skipped === 0) {
      text = op.reply
    } else {
      text = `已${verb} ${done} 笔`
      if (failed > 0) text += `，${failed} 笔失败（${firstError}）`
      if (skipped > 0) text += `，超出单次 ${MAX_BATCH_OPS} 笔上限，${skipped} 笔未处理`
    }
    appendAssistantMessage(text)
    if (done > 0) {
      void syncAfterWrite()
    }
  }, [pendingOp, bridge, categories, timezone, accounts, books, appendAssistantMessage, syncAfterWrite])

  /** T12-11：取消 pendingOp——不执行任何写，落一条「已取消」回执。 */
  const cancelOp = useCallback(() => {
    if (!pendingOp) return
    setPendingOp(null)
    appendAssistantMessage('已取消')
  }, [pendingOp, appendAssistantMessage])

  /**
   * T13-5（REQ-48 / 决策 #6 #8）：执行 pendingConfig——单桥命令 + 回执消息，
   * Kotlin 零改动（book.insert / account.insert / budget.insert / savings.insert /
   * meta.set 全部现成命令；入参组装口径照 BooksPage/AccountsPage/BudgetPage/
   * SavingsPage 现行 UI：id=newUuid()、ownerId='local'、version=1、mutationId UUID）。
   * 失败只报错不保留卡（重说一次即可重触发，与 pendingOp 收口口径一致）；
   * 成功统一 syncAfterWrite（savings/meta 仅本地，多跑一轮无副作用）。
   */
  const confirmConfig = useCallback(async () => {
    const pc = pendingConfig
    if (!pc || opBusyRef.current) return
    opBusyRef.current = true
    const { cfg, targets } = pc
    const yuan = (n: number) => `¥${n.toFixed(2)}`
    try {
      let text = ''
      switch (cfg.op) {
        case 'book.create': {
          await bridge.insertBook(
            {
              id: newUuid(), ownerId: 'local', name: cfg.name,
              icon: cfg.icon || '📒', version: 1,
            },
            newUuid(),
          )
          text = `已创建账本「${cfg.name}」`
          break
        }
        case 'account.create': {
          // REQ-69（T18-6）：分类是自由文案（与设置页同值域），**原样写入**——
          // 旧口径把它折成「收入账户/支出账户」，用户说的「储蓄卡」会丢失。
          // 余额同时真落库（T18-5 的 AccountEntity.balanceCents）；元→分，允许负值。
          const balanceCents = Math.round((cfg.initBalance ?? 0) * 100)
          await bridge.insertAccount(
            {
              id: newUuid(), ownerId: 'local', name: cfg.name,
              icon: '💰', accountType: cfg.accountType ?? null,
              balanceCents, version: 1,
            },
            newUuid(),
          )
          text = `已创建账户「${cfg.name}」`
          if (cfg.accountType) text += `（${cfg.accountType}）`
          if (cfg.initBalance !== undefined) {
            text += `；当前余额 ${formatSigned(balanceCents)}`
          }
          break
        }
        case 'budget.create': {
          // period 锚定创建当期（BudgetPage 同口径）：月度=YYYY-MM / 年度=YYYY
          const yearly = cfg.period === 'year'
          const today = todayBizDate(timezone)
          await bridge.insertBudget(
            {
              id: newUuid(),
              bookId: bookId!,
              budgetType: yearly ? 'annual' : 'monthly',
              categoryId: targets.categoryId ?? null,
              period: yearly ? today.slice(0, 4) : today.slice(0, 7),
              amountCents: Math.round((cfg.amount || 0) * 100),
              version: 1,
            },
            newUuid(),
          )
          text = `已为「${cfg.category}」创建${yearly ? '年度' : '月度'}预算 ${yuan(cfg.amount || 0)}`
          break
        }
        case 'savings.create': {
          const p = cfg.deadline
            ? deadlineToSavingsPeriod(cfg.deadline, todayBizDate(timezone))
            : null
          await bridge.insertSavingsGoal({
            id: newUuid(), ownerId: 'local', name: cfg.name,
            targetCents: Math.round((cfg.targetAmount || 0) * 100),
            currentCents: 0, icon: '🐷',
            periodValue: p?.value ?? null, periodUnit: p?.unit ?? null,
            version: 1,
          })
          text = `已创建存钱目标「${cfg.name}」（目标 ${yuan(cfg.targetAmount || 0)}）`
          break
        }
        case 'default.book_set': {
          await bridge.setMeta(META_KEY_DEFAULT_BOOK_ID, targets.bookId!)
          text = `已把「${cfg.name}」设为默认账本`
          break
        }
        case 'default.account_set': {
          await bridge.setMeta(META_KEY_DEFAULT_ACCOUNT_ID, targets.accountId!)
          text = `已把「${cfg.name}」设为默认账户`
          break
        }
      }
      setPendingConfig(null)
      appendAssistantMessage(text)
      // T13-5：写入成功 → 通知页面重拉对象名单（下一轮可点名本轮新建对象；
      // default.* 还会经重拉更新 bookId/bookName 落点）
      onConfigApplied?.()
      void syncAfterWrite()
    } catch (err) {
      const errMsg = localizeBridgeError(err instanceof Error ? err.message : '未知错误')
      setPendingConfig(null)
      appendAssistantMessage(`❌ 设置执行失败：${errMsg}`)
    } finally {
      opBusyRef.current = false
    }
  }, [pendingConfig, bridge, bookId, timezone, appendAssistantMessage, onConfigApplied, syncAfterWrite])

  /** T13-5：取消 pendingConfig——不落任何写，落一条「已取消」回执。 */
  const cancelConfig = useCallback(() => {
    if (!pendingConfig) return
    setPendingConfig(null)
    appendAssistantMessage('已取消')
  }, [pendingConfig, appendAssistantMessage])

  /**
   * T-M7-03e：中断态的重连入口（R-PROD-19）——把中断那一轮的输入**原样重发**。
   *
   * P2 只做到这一步：服务端本轮不落库（02f 的一轮原子写入），重发是安全且诚实的恢复
   * 手段。按 `conversation_id` + `seq` 的接续续跑归 P3 的 04b，那时的入口会换成
   * 「继续」而不是「重发」。
   */
  const retryInterrupted = useCallback(() => {
    const s = interrupted
    if (!s) return
    setInterrupted(null)
    void submitInput(s.input)
  }, [interrupted, submitInput])

  return {
    messages, phase, activeProposal, cardStatus, isSlow, pendingOp, pendingConfig,
    streaming, streamProjection: projection,
    submitInput, editProposal, undoExecution, confirmOp, cancelOp,
    confirmConfig, cancelConfig, cancelStream, rejectApproval,
    interrupted, retryInterrupted,
    appendAssistantMessage,
    deviceVoiceRequest, respondDeviceVoice, dismissDeviceVoice,
  }
}

function isOnline(): boolean {
  return typeof navigator !== 'undefined' ? navigator.onLine : true
}
