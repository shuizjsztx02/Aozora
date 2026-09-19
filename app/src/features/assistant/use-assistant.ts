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
 *     （取消的 `bridge.cancelAgent` 协议已就绪但本卡未接线——P1 无 UI 触发点）。
 *     本文件用**结构化窄接口 + 运行时能力探测**取用它们（见 `AgentStreamBridge`），
 *     因此桥未落地或 APK 版本落后时自动回落，不需要与 02h 并行改同一批文件。
 *   - 开关关闭**或桥不具备能力** → 整条回落既有 ai.interpret 单发链路，12 s / 25 s 体验不变。
 */

import { useState, useCallback, useRef, useEffect } from 'react'
import type { ActionProposal } from '../../domain/action-proposal'
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
import { newUuid } from '../../platform/uuid'
import { todayBizDate } from '../../domain/date'
import { META_KEY_DEFAULT_ACCOUNT_ID, META_KEY_DEFAULT_BOOK_ID } from '../../domain/meta'
import type {
  AccountData, BookData, BudgetData, CategoryData, SavingsGoalData, TransactionData,
} from '../../platform/bridge/bridge-types'
import type { AiInterpretContext, AiValidateVerdict } from '../../platform/bridge/bridge-types'
import type { Category } from '../../domain/types'

export interface AssistantMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  proposal?: ActionProposal
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
// T-M7-02i：agent 流式通道的消费契约
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
 */
export const AI_AGENT_CLIENT_ENABLED = false

/**
 * T-M7-02i：agent 流式通道的客户端桥契约（**由 T-M7-02h 实现**）。
 *
 * **为什么本文件自带一份、而不是 import `bridge-types.ts`**：本卡与 02h 并行推进，交付时
 * 桥的实现**尚未提交**——若 import 它的类型，HEAD 就处于「引用了尚不存在的符号」的不自洽
 * 状态（实测：远端 CI 的 typecheck 直接红）。所以这里用**结构化窄接口 + 运行时能力探测**：
 * 桥未落地时探测为假（整条回落单发链路，也顺带兜住「旧 APK 无这两条命令」），桥落地后
 * 自动接通。字段与 02h 的实现**逐一对齐**：`runAgent` 立即返回 `{pending, streamId}`
 * （「命令返回 pending + 事件推送结果」，同 `requestPermission`），事件带 `streamId`。
 *
 * **待 02h 提交后，这两处定义应合并到 `bridge-types.ts`**（那里才是契约类型的唯一源）——
 * 已登记为开放风险，别让它长期存在两份。
 */
export interface AgentStreamBridge {
  runAgent(
    input: string, bookId: string, conversationId?: string | null,
  ): Promise<{ pending: boolean; streamId: string }>
  onAiStream(listener: (event: AgentStreamEvent) => void): () => void
}

/** T-M7-02i：agent 流的下行事件（服务端 `stream.EventType` 的八类，02h 原样转发）。 */
export interface AgentStreamEvent {
  streamId: string
  event: 'phase' | 'tool_call' | 'tool_result' | 'confirm' | 'device_request' | 'delta' | 'done' | 'error'
  data: Record<string, unknown>
}

/** T-M7-02i：`done` 帧里本卡消费的字段（其余如 `usage` / `steps` / `pending_input` 不读）。
 *  `proposals` 是**仍待确认**的提案、`executions` 是**已执行**的回执，两者互斥
 *  （T-M7-02e 决策 2）。 */
export interface AgentStreamDone {
  conversation_id: string
  stop_reason: string
  proposals: Array<Record<string, unknown>>
  executions: Array<Record<string, unknown>>
}

/** T-M7-02i：流式期间的内存态气泡（**不落 messages store**）。
 *
 *  瞬时态不落盘与 T14-4 决策 #7 同口径（processing / pendingOp 等本就不进 store）；
 *  两个实际理由：① 一轮几十上百帧增量，逐帧写 localStorage 的序列化开销与帧数同阶；
 *  ② 架构 §8.5 明确「本轮不落库，重新打开会话时这半截文本不在历史里」——落盘会让
 *  用户重开 App 看到半截文本且没有失败标注。done / error 时定型成正式消息。 */
export interface StreamingBubble {
  /** 正文增量拼接（`delta.data.channel === 'content'`） */
  text: string
  /** 推理增量拼接（`delta.data.channel === 'reasoning'`；渲染为灰字折叠区，
   *  绝不混进正文——架构 §8.5） */
  reasoning: string
}

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
  activeProposal: ActionProposal | null
  cardStatus: CardStatus | null
  isSlow: boolean
  /** T12-11：edit/delete 意图圈定后的待确认操作（null=无）；确认/取消后清空。 */
  pendingOp: PendingOp | null
  /** T13-5：config 意图解析成功后的待确认设置操作（null=无）。 */
  pendingConfig: PendingConfig | null
  /** T-M7-02i：agent 流式期间的气泡（null=不在流式中）。**内存态**，不进 messages
   *  store——流式期间由页面渲染，done/error 时定型成正式消息后置回 null。 */
  streaming: StreamingBubble | null
  submitInput: (text: string) => Promise<void>
  /** 卡片编辑保存：softDelete 旧笔 + insert 新（编辑 = 替换写）。
   *  T12-9（REQ-35）：overrides = 卡内账本/预算 chip 覆盖（首提与替换写均生效）。 */
  editProposal: (edited: ActionProposal, overrides?: ProposalWriteOverrides) => Promise<void>
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
  const [phase, setPhase] = useState<AssistantPhase>('idle')
  const [activeProposal, setActiveProposal] = useState<ActionProposal | null>(null)
  const [cardStatus, setCardStatus] = useState<CardStatus | null>(null)
  const [isSlow, setIsSlow] = useState(false)
  // T12-11（REQ-37）：edit/delete 意图的待确认操作卡
  const [pendingOp, setPendingOp] = useState<PendingOp | null>(null)
  // T13-5（REQ-48）：config 意图的待确认设置卡
  const [pendingConfig, setPendingConfig] = useState<PendingConfig | null>(null)
  // T-M7-02i：agent 流式气泡（内存态，见 StreamingBubble 的说明）
  const [streaming, setStreaming] = useState<StreamingBubble | null>(null)
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
  // T-M7-02i：agent 会话 id（done 帧交回，下一轮必须带上，否则每轮都开新会话）
  const conversationIdRef = useRef<string | null>(null)
  // T-M7-02i：流式增量的累积缓冲——逐帧 setState 会拿到陈旧闭包，且高频重建对象；
  // 缓冲在 ref 里累积、每批一次性渲染（服务端已按 100 ms 合并窗把帧数降了一个数量级）
  const streamBufRef = useRef({ text: '', reasoning: '' })
  // T-M7-02i：本轮是否已收尾——done 与 error 只认先到的那个（桥命令的 resolve 也可能
  // 在事件之后到达，收尾必须幂等）
  const streamSettledRef = useRef(false)
  // T-M7-02i：当前流的退订函数（卸载/新提交时兜底退订，防事件打到已结束的轮次）
  const streamUnsubRef = useRef<(() => void) | null>(null)

  // C-C2 fix: 组件卸载时清理所有 timer
  useEffect(() => {
    return () => {
      if (slowTimerRef.current) clearTimeout(slowTimerRef.current)
      streamUnsubRef.current?.()
      streamUnsubRef.current = null
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

  /** 首次本地提交（解析成功后自动触发，无确认按钮）。
   *  T12-9：overrides.bookId 存在时视为卡内已选账本，hook 层 bookId 缺失也可写。 */
  const commitProposalInternal = useCallback(async (
    proposal: ActionProposal, overrides?: ProposalWriteOverrides,
  ) => {
    if (!bookId && !overrides?.bookId) { pushBookMissingError(proposal.proposalId); return }
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

      try { await bridge.triggerSync() } catch { /* ignore */ }
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
    }
  }, [bridge, bookId, pushBookMissingError, writeTransactions])

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
   * T-M7-02i：一轮 agent 流的收尾——卸载气泡，把产物落到既有链路上。
   *
   * `done === null` 表示本轮失败（`error` 帧或桥层异常）：**保留已渲染的半截文本并
   * 标注失败，不清空**（架构 §8.5——内容突然消失比「看到一半 + 一句失败提示」更糟）。
   * 失败轮服务端不落库（02f 的一轮原子写入），所以这半截文本只活在本地消息里，
   * 重开会话即不在；断连恢复归 P3。
   *
   * 成功轮的落点按 `done` 的产物三选一，顺序即优先级：
   *   ① `executions` 非空 → 服务端已执行（单 Step 高置信），落回执 + 记事务 id 供撤销；
   *   ② `proposals` 非空 → 挂起的提案（多步写入强制确认，T-M7-02e 决策 3），
   *      进 `pending-confirm` 卡态等用户点头——**不自动写库**；
   *   ③ 都没有 → 纯文本答复（chat / query），气泡定型成正式消息。
   * `proposals` 与 `executions` 互斥（T-M7-02e 决策 2），所以①②不会同时命中。
   */
  const settleStream = useCallback(async (done: AgentStreamDone | null) => {
    const { text } = streamBufRef.current
    streamUnsubRef.current?.()
    streamUnsubRef.current = null
    setStreaming(null)
    finishProcessing()

    if (!done) {
      appendAssistantMessage(text ? `${text}\n\n⚠️ 本轮未完成，请重试` : '⚠️ 本轮未完成，请重试')
      return
    }

    // 会话 id 必须留存：下一轮不带就会一直开新会话（02f 的会话索引靠它接续）
    conversationIdRef.current = done.conversation_id

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
      try { await bridge.triggerSync() } catch { /* ignore */ }
      return
    }

    const proposal = done.proposals[0] as unknown as ActionProposal | undefined
    if (proposal) {
      setMessages(prev => [...prev, {
        id: 'msg_' + Date.now() + '_assistant',
        role: 'assistant' as const,
        text: text || proposal.message,
        proposal,
        proposalStatus: 'pending' as const,
      }])
      setActiveProposal(proposal)
      // 挂起的提案 requiresConfirmation 恒为 True（T-M7-02e 决策 2），一律过确认门；
      // 用户点卡内「确认」后由 editProposal 的待确认分支接管首次写入。
      isPendingConfirmRef.current = true
      setCardStatus('pending-confirm')
      return
    }

    appendAssistantMessage(text || (done.stop_reason === 'empty_response'
      ? '这次没想出合适的回答，换个说法再试试？'
      : '本轮没能得出结论，换个说法再试试？'))
  }, [appendAssistantMessage, bridge, finishProcessing, setMessages])

  /**
   * T-M7-02i：agent 流式一轮（**开关开启时才走到这里**）。
   *
   * 只做消费：上行交给桥（T-M7-02h 的 `runAgent`），下行事件按类型分流，收尾统一走
   * settleStream。流的中间态**不做 intent 判定**——v2 intent 在流式中无法预知
   * （chat/query/edit/delete/config 的 UI 形态互异），只有 `done` 的产物决定落点
   * （架构 §9.1：复杂度对用户不可见）。
   */
  const runAgentTurn = useCallback(async (text: string) => {
    // 结构化窄接口 + 能力探测：桥（02h）未落地时本函数不会被调用（调用点已探过），
    // 这里再取一次是为了让类型系统认得这两个方法。
    const agentBridge = bridge as unknown as Partial<AgentStreamBridge>
    streamBufRef.current = { text: '', reasoning: '' }
    streamSettledRef.current = false
    setStreaming({ text: '', reasoning: '' })

    // 本轮流的归属 id（`runAgent` 的 ack 里给；事件里的 streamId 与它相等）。
    // ack 到达之前到达的事件按单流处理——submitInput 的并发互斥保证了 P1 同时只有一条流。
    let streamId: string | null = null

    const onEvent = (ev: AgentStreamEvent) => {
      if (streamSettledRef.current) return
      if (streamId && ev.streamId !== streamId) return
      if (ev.event === 'delta') {
        const d = ev.data as { text?: unknown; channel?: unknown }
        if (typeof d.text !== 'string') return
        // 两个通道分流：reasoning 进灰字折叠区，绝不混进正文（架构 §8.5）
        if (d.channel === 'reasoning') streamBufRef.current.reasoning += d.text
        else streamBufRef.current.text += d.text
        setStreaming({ ...streamBufRef.current })
        return
      }
      if (ev.event === 'done') {
        streamSettledRef.current = true
        void settleStream(ev.data as unknown as AgentStreamDone)
        return
      }
      if (ev.event === 'error') {
        // 服务端安全枚举与桥侧本地失败（timeout / network / auth_lost / …）对用户是
        // 同一件事——本轮没做成。文案不透传（安全口径），统一走失败收尾。
        streamSettledRef.current = true
        void settleStream(null)
        return
      }
      // phase / tool_call / tool_result / confirm / device_request：P1 最小形态不渲染
      // （步骤卡片流归 P2）。confirm 的提案不进 UI——done.proposals 才是权威清单，
      // 按 proposalId 去重是收敛后的形态，中途那一帧只是实时信号。
    }

    streamUnsubRef.current?.()
    streamUnsubRef.current = agentBridge.onAiStream!(onEvent)

    try {
      const ack = await agentBridge.runAgent!(text, bookId!, conversationIdRef.current)
      streamId = ack.streamId
    } catch {
      // 桥层失败（旧 APK 无该命令 / 链路建不起来）：同样按本轮失败收尾
      if (!streamSettledRef.current) {
        streamSettledRef.current = true
        await settleStream(null)
      }
    }
    // 命令是「立即返回 ack + 事件推送」的形态（架构 §8.2），所以这里**不退订**：
    // 本轮的结束以 done / error 为准，那两个分支各自负责退订。
  }, [bridge, bookId, settleStream])

  const submitInput = useCallback(async (text: string) => {
    if (!text.trim()) return
    // C-B3 fix: 并发提交互斥 — 非 idle 阶段拒绝新提交
    if (isSubmittingRef.current) return
    isSubmittingRef.current = true

    // T12-11：新输入接管会话，未决操作卡作废（不静默执行）
    // T13-5：config 预览卡同理
    setPendingOp(null)
    setPendingConfig(null)

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
    const needsAI = looksLikeOp ||
      localProposal.confidence < CONFIDENCE_THRESHOLD ||
      localProposal.warnings.length > 0 || localProposal.action === 'chat'

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
    buildParseContext, commitProposalInternal, finishProcessing, runAgentTurn,
    appendAssistantMessage, answerTextIntent, openOpPreview, openConfigPreview,
  ])

  /** 卡片编辑保存：替换写（softDelete 旧 + insert 新），不新增 transaction.update。
   *  T12-9（REQ-35）：overrides 透传给首提与替换写（卡内切账本 = 该笔改挂新账本）。 */
  const editProposal = useCallback(async (
    edited: ActionProposal, overrides?: ProposalWriteOverrides,
  ) => {
    if (!bookId && !overrides?.bookId) { pushBookMissingError(edited.proposalId); return }
    // T12-8（REQ-30 ①）：待选分类态——分类补齐前编辑只回写消息（不写库、态保持），
    // 选定分类后接管首次写入（commitProposalInternal：文案「已记录 N 笔」+ triggerSync）
    if (isPendingCategoryRef.current) {
      setActiveProposal(edited)
      setMessages(prev => prev.map(m =>
        m.proposal?.proposalId === edited.proposalId ? { ...m, proposal: edited } : m
      ))
      if ((edited.transactions ?? []).some(t => !t.categoryId)) return
      isPendingCategoryRef.current = false
      await commitProposalInternal(edited, overrides)
      return
    }
    // T-M7-02i：待确认态（agent 挂起的提案）——用户点了卡内「确认」即接管首次写入。
    // 与上一段同构，差别只在触发条件：这里不要求补齐任何字段，因为服务端已经组装并
    // 校验过提案，用户点头就是唯一前置（T-M7-02e 决策 3：多步写入一律强制确认）。
    if (isPendingConfirmRef.current) {
      isPendingConfirmRef.current = false
      setActiveProposal(edited)
      setMessages(prev => prev.map(m =>
        m.proposal?.proposalId === edited.proposalId ? { ...m, proposal: edited } : m
      ))
      await commitProposalInternal(edited, overrides)
      return
    }
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
      try { await bridge.triggerSync() } catch { /* ignore */ }
    } catch (err) {
      const errMsg = localizeBridgeError(err instanceof Error ? err.message : '未知错误')
      setMessages(prev => prev.map(m =>
        m.proposal?.proposalId === edited.proposalId
          ? { ...m, proposalStatus: 'error' as const, text: `❌ 保存失败：${errMsg}` } : m
      ))
      setCardStatus('error')
      setPhase('idle')
    }
  }, [bridge, bookId, pushBookMissingError, writeTransactions, commitProposalInternal])

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
      try { await bridge.triggerSync() } catch { /* ignore */ }
    } catch {
      // C-B1 fix: undo 失败时通知用户，不静默吞掉
      setMessages(prev => prev.map(m =>
        m.transactionIds && m.transactionIds.some(id => ids.includes(id))
          ? { ...m, proposalStatus: 'error' as const, text: '撤销失败，请手动处理' } : m
      ))
    }
    setPhase('idle')
  }, [bridge])

  /**
   * T12-11（REQ-37 ③ / 决策 #3 #5）：执行 pendingOp——逐笔 updateTransaction/
   * softDeleteTransaction（各自 mutationId UUID，上限 MAX_BATCH_OPS）。
   * 逐笔独立、部分失败可见不回滚（与手动记账「逐笔入队、失败可见」同口径）；
   * 成功回执优先用云端 reply（仅全成功无截断时），否则模板语；有写入才 triggerSync。
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
      bridge.triggerSync().catch(() => { /* 同步入队失败不阻断（hook 层同口径） */ })
    }
  }, [pendingOp, bridge, categories, timezone, accounts, books, appendAssistantMessage])

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
   * 成功统一 triggerSync（savings/meta 仅本地，触发无副作用）。
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
          // config 契约 accountType 是 income|expense 二值（T13-4 prompt）；native
          // accountType 是自由文案分类（储蓄卡/微信/…），折成中文标签再写。
          const typeLabel = cfg.accountType === 'income' ? '收入账户' : '支出账户'
          await bridge.insertAccount(
            {
              id: newUuid(), ownerId: 'local', name: cfg.name,
              icon: '💰', accountType: typeLabel, version: 1,
            },
            newUuid(),
          )
          text = `已创建账户「${cfg.name}」（${typeLabel}）`
          if (cfg.initBalance) {
            text += `；期初余额 ${yuan(cfg.initBalance)} 请再记一笔写入（账户暂无期初字段）`
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
      bridge.triggerSync().catch(() => { /* 同步入队失败不阻断（confirmOp 同口径） */ })
    } catch (err) {
      const errMsg = localizeBridgeError(err instanceof Error ? err.message : '未知错误')
      setPendingConfig(null)
      appendAssistantMessage(`❌ 设置执行失败：${errMsg}`)
    } finally {
      opBusyRef.current = false
    }
  }, [pendingConfig, bridge, bookId, timezone, appendAssistantMessage, onConfigApplied])

  /** T13-5：取消 pendingConfig——不落任何写，落一条「已取消」回执。 */
  const cancelConfig = useCallback(() => {
    if (!pendingConfig) return
    setPendingConfig(null)
    appendAssistantMessage('已取消')
  }, [pendingConfig, appendAssistantMessage])

  return {
    messages, phase, activeProposal, cardStatus, isSlow, pendingOp, pendingConfig,
    streaming,
    submitInput, editProposal, undoExecution, confirmOp, cancelOp,
    confirmConfig, cancelConfig,
    appendAssistantMessage,
  }
}

function isOnline(): boolean {
  return typeof navigator !== 'undefined' ? navigator.onLine : true
}
