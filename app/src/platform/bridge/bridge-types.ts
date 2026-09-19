/**
 * 桥命令类型定义 — 与 Kotlin 侧 BridgeCommands / BridgeProtocol 一一对应。
 *
 * 这是客户端与原生桥的唯一契约类型源。
 * 任何命令的新增/修改必须同步更新 Kotlin 侧 BridgeCommands.kt。
 */

/** 桥协议版本 */
export const BRIDGE_VERSION = 1

/** 所有支持的命令名 */
export const BridgeCommands = {
  GET_VERSION: 'bridge.getVersion',

  // Transaction
  TX_INSERT: 'transaction.insert',
  TX_GET: 'transaction.get',
  TX_LIST_BY_BOOK: 'transaction.listByBook',
  TX_LIST_BY_DATE: 'transaction.listByDate',
  TX_SOFT_DELETE: 'transaction.softDelete',
  // 第十二轮 T12-7（REQ-36 / 决策 #1：桥计数本轮唯一变更点 74→75）：交易编辑真 update
  TX_UPDATE: 'transaction.update',

  // Book
  BOOK_LIST: 'book.list',
  BOOK_INSERT: 'book.insert',
  // 第十四轮 T14-2（REQ-39 / 决策 #1：桥计数本轮唯一变更点 75→76）：账本编辑
  BOOK_UPDATE: 'book.update',

  // Category
  CATEGORY_LIST: 'category.list',
  // 第八轮 REQ-8：分类自定义（新建/编辑）+ 照片图标选择
  CATEGORY_INSERT: 'category.insert',
  CATEGORY_UPDATE: 'category.update',
  ICON_PICK: 'icon.pick',
  // 第十六轮 T16-5（REQ-61 / 决策 #7：桥计数本轮唯一变更点 76→77）：
  // 主题自定义背景选图（独立压缩参数，结果只落 IndexedDB）
  THEME_PICK_BACKGROUND: 'theme.pickBackground',

  // 第八轮 REQ-7：存钱目标（仅本地，不同步）
  SAVINGS_LIST: 'savings.list',
  SAVINGS_INSERT: 'savings.insert',
  SAVINGS_UPDATE: 'savings.update',
  SAVINGS_ARCHIVE: 'savings.archive',

  // Account
  ACCOUNT_LIST: 'account.list',
  ACCOUNT_INSERT: 'account.insert',
  ACCOUNT_UPDATE: 'account.update',
  ACCOUNT_ARCHIVE: 'account.archive',

  // Budget
  BUDGET_LIST: 'budget.list',
  BUDGET_INSERT: 'budget.insert',
  BUDGET_UPDATE: 'budget.update',
  BUDGET_DELETE: 'budget.delete',

  // Pending Mutations
  PM_LIST: 'pendingMutation.list',
  PM_MARK_SYNCING: 'pendingMutation.markSyncing',
  PM_MARK_FAILED: 'pendingMutation.markFailed',
  PM_REMOVE: 'pendingMutation.remove',

  // T-M3-02: 同步引擎
  SYNC_GET_STATUS: 'sync.getStatus',
  SYNC_TRIGGER: 'sync.trigger',

  // T-M4-05a: 提醒调度
  REMINDER_GET_STATUS: 'reminder.getStatus',
  REMINDER_SET: 'reminder.set',
  REMINDER_CHECK_PERMISSION: 'reminder.checkPermission',

  // T-M4-03c: AI 洞察（ADR-003 通道）
  AI_REPORT: 'ai.report',

  // T-M4-02c: AI 助手对话流（ADR-003 通道）
  AI_INTERPRET: 'ai.interpret',
  AI_VALIDATE: 'ai.validate',
  AI_EXECUTE: 'ai.execute',

  // T-M7-02h（桥计数 77→79）：agent 流式通道。ai.agent 立即应答
  // {pending, streamId}，事件经 window.__aozoraAiStream__ 推送；
  // ai.cancel 关掉 SSE 长连接（服务端把断连当取消）。
  AI_AGENT: 'ai.agent',
  AI_CANCEL: 'ai.cancel',

  // T-M7-03d（桥计数 79→82）：会话索引三命令，对接 /ai/conversations。
  // 请求-响应形态（走 25 s 单发预算）。「新建会话」不需要命令——服务端在首轮
  // 不带 conversation_id 时自建（02f 口径），客户端只是切到本地会话桶。
  CONVERSATION_LIST: 'conversation.list',
  CONVERSATION_RENAME: 'conversation.rename',
  CONVERSATION_DELETE: 'conversation.delete',

  // T-M4-04a: 权限管理
  PERMISSIONS_GET_STATUS: 'permissions.getStatus',
  PERMISSIONS_REQUEST: 'permissions.request',
  PERMISSIONS_OPEN_SETTINGS: 'permissions.openSettings',

  // T-M4-04b 占位：语音
  VOICE_START_RECORDING: 'voice.startRecording',
  VOICE_STOP_RECORDING: 'voice.stopRecording',

  // T-M4-04d: 图片链路
  IMAGE_PICK: 'image.pick',
  IMAGE_DELETE: 'image.delete',

  // T-M4-04e: 图片记账解析
  IMAGE_PARSE: 'image.parse',
  IMAGE_PARSE_CANCEL: 'image.parse_cancel',

  // T-M5-04a: 隐私同意
  CONSENT_RECORD: 'consent.record',
  CONSENT_STATUS: 'consent.status',

  // T-M5-04b: 数据导出
  EXPORT_CREATE: 'export.create',
  EXPORT_LIST: 'export.list',
  EXPORT_GET: 'export.get',
  EXPORT_DOWNLOAD: 'export.download',

  // T-M5-04c: 账号删除
  ACCOUNT_REQUEST_DELETION_TOKEN: 'account.requestDeletionToken',
  ACCOUNT_DELETE: 'account.delete',

  // T-M5-05c: 应用内反馈
  FEEDBACK_SUBMIT: 'feedback.submit',

  // FIX-002/003/004（真机修复轮）：触感 / 分享 / 认证状态
  HAPTIC_FEEDBACK: 'haptic.feedback',
  SHARE_TEXT: 'share.text',
  AUTH_GET_STATUS: 'auth.getStatus',
  AUTH_TRIGGER_SYNC: 'auth.triggerSync',

  // 第十轮 T10-3（REQ-19，决策 #4）：登录与安全（手机号登录/登出/资料）
  AUTH_LOGIN: 'auth.login',
  AUTH_LOGOUT: 'auth.logout',
  AUTH_ME: 'auth.me',
  AUTH_UPDATE_PROFILE: 'auth.updateProfile',

  // T9-5a（REQ-11，决策 #1）：本地展示元数据（默认选择 + 列表顺序，存 sync_meta，不上行）
  META_GET: 'meta.get',
  META_SET: 'meta.set',

  // 第十一轮 T11-2（REQ-24，决策 #5）：桌面小组件手动刷新（配置保存后即时重绘）
  WIDGET_REFRESH: 'widget.refresh',

  // 第十一轮 T11-4/T11-5（REQ-26，决策 #4）：定时记账规则（仅本地，不同步）
  SCHEDULED_LIST: 'scheduled.list',
  SCHEDULED_INSERT: 'scheduled.insert',
  SCHEDULED_UPDATE: 'scheduled.update',
  SCHEDULED_ARCHIVE: 'scheduled.archive',

  // 第十一轮 T11-7（REQ-28，决策 #8）：聊天角色（仅本地，不同步；
  // 当前角色指针 = meta.active_persona_id，走 meta.* 命令）
  PERSONA_LIST: 'persona.list',
  PERSONA_INSERT: 'persona.insert',
  PERSONA_UPDATE: 'persona.update',
  PERSONA_ARCHIVE: 'persona.archive',
} as const

export type BridgeCommandName = typeof BridgeCommands[keyof typeof BridgeCommands]

/** 桥响应结构 */
export interface BridgeResponse<T = unknown> {
  requestId: string
  success: boolean
  data?: T
  error?: {
    code: string
    message: string
  }
}

/** 桥错误 */
export class BridgeError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly requestId: string
  ) {
    super(message)
    this.name = 'BridgeError'
  }
}

// ─── 数据传输类型 ───

export interface TransactionData {
  id: string
  bookId: string
  type: 'expense' | 'income'
  amountCents: number
  categoryId: string
  accountId?: string | null
  note: string
  bizDate: string
  occurredAt: number
  source: 'manual' | 'nlu_local' | 'nlu_ai' | 'import' | 'scheduled'
  /** 第十轮 REQ-17（决策 #1）：是否计入预算；缺省 = 计入（!== false 口径）。 */
  includeInBudget?: boolean
  version: number
  deletedAt: number | null
}

export interface TransactionInput {
  id: string
  bookId: string
  type: 'expense' | 'income'
  amountCents: number
  categoryId: string
  accountId?: string | null
  note?: string
  bizDate: string
  occurredAt: number
  source: 'manual' | 'nlu_local' | 'nlu_ai' | 'import' | 'scheduled'
  /** 手动记账显式传值（QuickEntrySheet 按钮初始 false）；聊天/批量不传 → 计入。 */
  includeInBudget?: boolean
  version?: number
}

/** 第十轮 T10-3（REQ-19，决策 #4）：auth.login 响应。 */
export interface AuthLoginResult {
  userId: string
  isNewUser: boolean
}

/** auth.me / auth.updateProfile 响应 = 服务端 MeResponse 透传（snake_case 时间戳）。 */
export interface MeData {
  id: string
  nickname: string | null
  phone: string | null
  status: string
  timezone: string
}

export interface BookData {
  id: string
  ownerId: string
  name: string
  icon: string
  version: number
  /** T7-2（REQ-6，决策 #1）：true = 总账本（查询时聚合，不存流水）。 */
  isMaster?: boolean
  /** T9-5a（REQ-11，决策 #2）：true = 系统默认记账账本（master 恒不为 true）。 */
  isDefault?: boolean
}

export interface BookInput {
  id: string
  ownerId: string
  name: string
  icon?: string
  version?: number
}

export interface CategoryData {
  id: string
  kind: 'expense' | 'income'
  name: string
  parentId: string | null
  /** 第八轮 REQ-8：自定义图标（emoji 或 data:image base64 URI）；null=按名映射。 */
  icon?: string | null
  version: number
}

/** 第八轮 REQ-8：category.insert / category.update 入参。 */
export interface CategoryInput {
  id: string
  ownerId?: string
  kind: 'expense' | 'income'
  name: string
  parentId?: string | null
  icon?: string | null
  version: number
}

/** 第八轮 REQ-8：icon.pick 返回（base64 data URI，无服务端上传）。 */
export interface IconPickResult {
  dataUri: string
  size: number
}

/** 第十六轮 T16-5（REQ-61）：theme.pickBackground 返回（全屏背景 data URI）。 */
export interface ThemePickBackgroundResult {
  dataUri: string
  size: number
}

/** 第八轮 REQ-7：存钱目标（仅本地）；T9-4（REQ-14）增存款期限。 */
export interface SavingsGoalData {
  id: string
  name: string
  targetCents: number
  currentCents: number
  icon: string | null
  /** 存款期限数值；null = 无期限 */
  periodValue: number | null
  /** 期限单位，仅此二值 */
  periodUnit: 'week' | 'month' | null
  createdAt: number
  updatedAt: number
  version: number
}

/** savings.insert / savings.update 入参（包裹在 {goal} 内，无 mutationId）。 */
export interface SavingsGoalInput {
  id: string
  ownerId?: string
  name: string
  targetCents: number
  currentCents?: number
  icon?: string | null
  /** value+unit 成对给或成对省；native 校验 unit 二值 + 范围（周 1-521 / 月 1-120） */
  periodValue?: number | null
  periodUnit?: 'week' | 'month' | null
  createdAt?: number
  version: number
}

/**
 * 第十一轮 T11-4（REQ-26 / 决策 #4）：定时记账规则（仅本地，不同步）。
 * lastRunDate 由引擎专管（用户编辑不回传该键）；日期均为 'YYYY-MM-DD'。
 * runHour/runMinute = 执行时刻（第十四轮 T14-5 / REQ-43），引擎到点才生成。
 */
export interface ScheduledRuleData {
  id: string
  ownerId: string
  type: 'expense' | 'income'
  name: string
  amountCents: number
  bookId: string
  categoryId: string
  accountId: string | null
  includeInBudget: boolean
  note: string
  repeat: 'daily' | 'weekly' | 'monthly' | 'yearly'
  startDate: string
  /** null = 无限制 */
  endDate: string | null
  enabled: boolean
  /** 最近一次生成交易的 bizDate；null = 从未执行 */
  lastRunDate: string | null
  /** 执行时刻-时 0..23（v9 存量规则=8） */
  runHour: number
  /** 执行时刻-分 0..59（v9 存量规则=0） */
  runMinute: number
  createdAt: number
  updatedAt: number
  version: number
}

/**
 * scheduled.insert / scheduled.update 入参（包裹在 {rule} 内，无 mutationId）。
 * update 为局部合并语义：键缺省保留原值；accountId/endDate 显式 null = 清除
 * （enabled 快捷开关只传 {id, enabled, version}）。runHour/runMinute 缺省
 * 8/0（insert）／保留原值（update）；值域 0..23 / 0..59 由 native 校验。
 */
export interface ScheduledRuleInput {
  id: string
  ownerId?: string
  type?: 'expense' | 'income'
  name?: string
  amountCents?: number
  bookId?: string
  categoryId?: string
  accountId?: string | null
  includeInBudget?: boolean
  note?: string
  repeat?: 'daily' | 'weekly' | 'monthly' | 'yearly'
  startDate?: string
  endDate?: string | null
  enabled?: boolean
  runHour?: number
  runMinute?: number
  createdAt?: number
  version: number
}

/**
 * 聊天角色行（第十一轮 T11-7 / REQ-28 / 决策 #8）。仅本地、不上行（镜像
 * SavingsGoalData/ScheduledRuleData 的毒丸理由）。persona.list 输出全字段。
 */
export interface PersonaData {
  id: string
  /** 昵称，≤16 字 */
  name: string
  /** emoji 或 data:image base64（IconPicker 产物），可空 */
  avatar: string | null
  /** 人设文本，≤2000 字；注入在线 AI system 尾部 */
  persona: string
  createdAt: number
  updatedAt: number
  version: number
}

/**
 * persona.insert / persona.update 入参（包裹在 {persona} 内，无 mutationId）。
 * update 为局部合并语义：键缺省保留原值；avatar 显式 null = 清除回退 emoji/占位。
 */
export interface PersonaInput {
  id: string
  name?: string
  avatar?: string | null
  persona?: string
  version: number
}

export interface AccountData {
  id: string
  name: string
  icon: string
  version: number
  /** T7-2（REQ-5）：账户分类（储蓄卡/微信/…或自定义），旧行为 null。 */
  accountType?: string | null
  /** T9-5a（REQ-11，决策 #2）：true = 系统默认账户。 */
  isDefault?: boolean
}

/** T7-2（REQ-5）：account.insert / account.update 共用入参。 */
export interface AccountInput {
  id: string
  ownerId: string
  name: string
  icon?: string
  /** 显式传 null 清空分类；键缺省保留当前值（update 局部合并语义）。 */
  accountType?: string | null
  version: number
}

export interface BudgetData {
  id: string
  bookId: string
  budgetType: 'monthly' | 'annual'
  categoryId: string | null
  period: string
  amountCents: number
  version: number
}

export interface PendingMutationData {
  mutationId: string
  entityType: string
  entityId: string
  operation: 'insert' | 'update' | 'delete' | 'archive'
  payload: string
  status: 'pending' | 'syncing' | 'failed'
  retryCount: number
  createdAt: number
  /** T-M3-02: 同步期望版本号（insert 时为 null）。 */
  expectedVersion?: number | null
  /** T-M3-02: 最近一次失败原因；成功 / 待处理时为 null。 */
  lastError?: string | null
}

// ─── T-M3-02: 同步引擎类型 ───

export type SyncStateValue = 'idle' | 'pushing' | 'pulling' | 'rebuilding' | 'auth-lost' | 'error'

/**
 * 被服务端拒绝的 mutation（终态，不再自动重试）。
 * N-M3-5 修复：前端可据此展示"X 条变更冲突，待处理"提示。
 */
export interface FailedMutationData {
  mutationId: string
  entityType: string
  entityId: string
  operation: 'insert' | 'update' | 'delete' | 'archive'
  /** 服务端拒绝原因：expected_version_mismatch | not_found | forbidden | invalid_payload。 */
  reason?: string | null
}

export interface SyncStatusData {
  state: SyncStateValue
  pendingCount: number
  lastSyncedAt: number
  lastError?: string
  /** N-M3-5：终态失败的 mutation 列表。 */
  failedMutations: FailedMutationData[]
}

/** 原生 → Web 同步事件（通过 window.__aozoraSyncEvent__ 推送）。 */
export interface SyncEvent extends SyncStatusData {}

// ─── T-M4-05a: 提醒调度类型 ───

export interface ReminderStatusData {
  enabled: boolean
  hour: number
  minute: number
  notificationPermission: boolean
}

// ─── T-M4-02c: AI 助手对话类型 ───

/** 第十一轮 T11-6（REQ-27 / 决策 #7）：ai.interpret 注入上下文。
 *  随 payload.context 透传至服务端拼入 system 消息；两字段均可缺省/null（服务端零再改）。
 *  persona 段由 T11-7 消费，本键位仅预留形状。
 *  第十二轮 T12-11（REQ-37 / 决策 #3）：+categories（分类名白名单，prompt v2 约束
 *  category/filter.category/set.category 从中选）与 today（相对日期换算基准）；
 *  均可缺省 → 服务端渲染段跳过，行为与旧版一致。桥侧 optJSONObject 原样透传，Kotlin 零改动。
 *  第十三轮 T13-5（REQ-48 / 决策 #7）：+books/accounts/budgets/goals 现有对象名单
 *  （只给名字，≤30 条 × ≤16 字，服务端 _render_objects_section 渲染「现有对象」段，
 *  config 意图的点名与避重名依据）；均可缺省/null → 段跳过。仍零 Kotlin 改动。 */
export interface AiInterpretContext {
  persona: { name: string; text: string } | null
  profile: Record<string, string> | null
  /** 用户现有分类名（≤120 个、每个 ≤16 字，服务端另有独立截断兜底） */
  categories?: string[] | null
  /** 用户时区今天（YYYY-MM-DD），prompt v2 相对日期换算用 */
  today?: string | null
  /** T13-5：现有对象名单（只含名字；模型永不经手 id，名→id 由客户端解析）。
   *  T15-7（REQ-56）起同时是 edit 契约 v4 的 accountName/bookName 点名依据。 */
  books?: Array<{ name: string }> | null
  accounts?: Array<{ name: string }> | null
  budgets?: Array<{ name: string }> | null
  goals?: Array<{ name: string }> | null
}

export interface AiInterpretResponse {
  request_id: string
  task: string
  model: string
  output: Record<string, unknown>
  usage: { prompt_tokens: number; completion_tokens: number }
}

export type AiValidateVerdict =
  | 'accepted'
  | 'rejected_hallucination'
  | 'rejected_schema'
  | 'rejected_low_confidence'

export interface AiValidateError {
  path: string
  message: string
}

export interface AiValidateResponse {
  valid: boolean
  errors: AiValidateError[]
  verdict: AiValidateVerdict
}

export interface AiExecuteResponse {
  transaction_ids: string[]
  proposal_id: string
  undo_window_seconds: number
}

// ─── T-M7-02h: agent 流式通道 ───

/**
 * ai.agent 参数。`conversation_id` 缺省 = 服务端新建会话，其 id 随 `done` 帧
 * 回传，**下一轮必须带上**（否则每轮都开新会话、多轮上下文断裂）。
 */
export interface AiAgentParams {
  input: string
  bookId: string
  conversationId?: string | null
}

/** ai.agent 的即时应答——事件随后经 `__aozoraAiStream__` 推送。 */
export interface AiAgentAck {
  pending: true
  /** = 本轮 ai.agent 的 requestId；事件里的 streamId 与之相等。 */
  streamId: string
}

/**
 * 流事件名：架构 §8.1 的八类。P1 实际产出六类（`device_request` 无生产者，
 * `routing` 属域路由）；`error` 另有一路来自桥侧本地失败（链路断开 / 认证失效）。
 */
export type AiStreamEventName =
  | 'phase'
  | 'tool_call'
  | 'tool_result'
  | 'confirm'
  | 'device_request'
  | 'delta'
  | 'done'
  | 'error'

/** `done` 帧载荷（权威字段表见 `server/src/modules/ai/stream.py`）。 */
export interface AiStreamDoneData {
  conversation_id: string
  stop_reason: string
  steps: number
  tool_calls: number
  writes: number
  usage: { prompt_tokens: number; completion_tokens: number }
  proposals: Array<Record<string, unknown>>
  executions: Array<Record<string, unknown>>
  /**
   * 本轮**没轮上处理**的中途输入（02d 的约定：调用方必须消费——非空时客户端应把
   * 这些话**另起一轮**重发，不能当作没说过）。P1 的端点不接收中途输入，故恒为空数组；
   * 服务端载荷是 `list(result.pending_input)`，即**数组**（02i 交接的跨卡修正）。
   */
  pending_input: string[]
}

/**
 * `error` 帧载荷。`code` 有两族：服务端安全枚举（`ai_unavailable` /
 * `agent_failed`）与桥侧本地失败（`timeout` / `network` / `auth_lost` /
 * `429` / `503` / `stream_ended` / `not_found`）。
 *
 * `not_found`（T-M7-03d）：本轮带的 `conversation_id` 已失效——服务端在**开流之前**
 * 就回 404（别人的 / 已删除的 / 不存在的 id 同一个码）。客户端据此丢弃该 id 并开新
 * 会话，而不是提示用户重试。
 */
export interface AiStreamErrorData {
  code: string
  message: string
  conversation_id?: string | null
}

/** 原生 → Web 的 agent 流事件（Android 通过 `window.__aozoraAiStream__` 推送）。 */
export interface AiStreamEvent {
  streamId: string
  event: AiStreamEventName
  data: Record<string, unknown>
}

// ─── T-M7-03d: 会话索引（/ai/conversations） ───

/**
 * 会话索引项（服务端 `AIConversationSummary`）。
 *
 * 字段**原样 snake_case**——本仓没有 snakeToCamel 转换层，`/ai/*` 的响应类型一律
 * 按服务端拼写声明（同 `AiExecuteResponse.transaction_ids`）。
 */
export interface AiConversationSummary {
  id: string
  /** 服务端按首轮输入派生的标题；**存量会话升级后为空串**（客户端兜底文案渲染） */
  title: string
  /** 建会话时落点的账本；**只作展示与归属记录，不驱动客户端的记账账本**（03d 规则） */
  book_id: string | null
  created_at: string
  /** 列表排序键（服务端保证非 null：未写入时回退 `created_at`） */
  last_active_at: string
}

/** `conversation.list` 的响应。 */
export interface AiConversationListResponse {
  conversations: AiConversationSummary[]
}

export interface AiCategoryFact {
  category_id: string
  category_name: string
  total_cents: number
  count: number
}

export interface AiBudgetFact {
  amount_cents: number
  spent_cents: number
  remaining_cents: number
  pct_used: number
}

export interface AiTransactionFact {
  id: string
  amount_cents: number
  category_name: string
  note: string
  biz_date: string
}

export interface AiTrendFact {
  period: string
  total_income: number
  total_expense: number
  balance: number
  transaction_count: number
}

export interface AiReportResponse {
  request_id: string
  book_id: string
  period: string
  data_hash: string
  total_income: number
  total_expense: number
  balance: number
  transaction_count: number
  expense_by_category: AiCategoryFact[]
  income_by_category: AiCategoryFact[]
  budget: AiBudgetFact | null
  largest_expense: AiTransactionFact | null
  largest_income: AiTransactionFact | null
  trend: AiTrendFact[]
  narrative: string | null
  narrative_cached: boolean
  usage: { prompt_tokens: number; completion_tokens: number } | null
}

// ─── T-M4-04a: 权限管理类型 ───

export type PermissionType = 'RECORD_AUDIO' | 'CAMERA' | 'READ_MEDIA_IMAGES' | 'POST_NOTIFICATIONS'

export type PermissionStatus = 'not_requested' | 'granted' | 'denied' | 'permanently_denied'

export interface PermissionStatusData {
  permissions: Record<PermissionType, PermissionStatus>
  canRecordAudio: boolean
  canUseCamera: boolean
  canAccessImages: boolean
}

/** 权限异步结果推送（Android 权限对话框关闭后通过 __aozoraPermissionResult__ 推送）。 */
export interface PermissionResultEvent {
  permission: PermissionType
  status: PermissionStatus
}

// ─── T-M4-04d: 图片链路类型 ───

export type ImageSource = 'camera' | 'gallery'

export interface ImagePickOptions {
  source: ImageSource
  /** 压缩质量 1-100（默认 80）。 */
  quality?: number
}

export interface ImagePickResult {
  attachmentId: string
  mimeType: string
  fileSize: number
}

/** 上传进度推送（Android 通过 window.__aozoraImageProgress__ 推送）。 */
export interface ImageProgressEvent {
  sent: number
  total: number
}

// ─── T-M4-04e: 图片记账解析类型 ───

export interface ParseImageParams {
  attachmentId: string
  bookId: string
}

/** 服务端 /ai/parse-image 返回的解析结果。 */
export interface ParseImageResult {
  request_id: string
  /** 完整 ActionProposal（action: add_tx_batch）。 */
  proposal: Record<string, unknown>
  attachment_id: string
  confidence: number
  usage: { prompt_tokens: number; completion_tokens: number }
}

/** parseImage 桥错误码。 */
export type ParseImageErrorCode =
  | 'network_error'
  | 'server_error'
  | 'auth_lost'
  | 'no_ai_provider'
  | 'attachment_not_found'
  | 'attachment_not_pending'
  | 'attachment_expired'
  | 'file_missing'
  | 'image_too_large'
  | 'parse_failed'

// ─── T-M5-04a: 隐私同意类型 ───

export interface ConsentRecordParams {
  policyVersion?: string
  consentedAt?: number
  deviceId?: string
}

export interface ConsentRecordResult {
  id: string
  policyVersion: string
  consentedAt: number
}

export interface ConsentStatusResult {
  hasConsented: boolean
}

// ─── T-M5-04b: 数据导出类型 ───

export type ExportFormat = 'json' | 'csv'
export type ExportStatus = 'pending' | 'completed' | 'failed' | 'expired'

export interface ExportData {
  id: string
  bookId: string
  format: ExportFormat
  status: ExportStatus
  createdAt: string
  expiresAt?: string
  fileSize?: number
}

export interface ExportCreateParams {
  bookId: string
  format?: ExportFormat
}

export interface ExportDownloadResult {
  localPath: string
  filename: string
  /** 文件文本内容（≤ 20MB 时返回）；用于前端对账。 */
  content?: string
  /** F-M5-04b-F3: > 20MB 时为 true，前端应跳过对账并提示用户。 */
  contentTruncated?: boolean
}

/** export.download 桥错误码。 */
export type ExportErrorCode =
  | 'network_error'
  | 'server_error'
  | 'auth_lost'
  | 'not_found'
  | 'expired'
  | 'invalid_input'
  | 'unavailable'

// ─── T-M5-04c: 账号删除类型 ───

export interface AccountDeletionTokenResult {
  confirmToken: string
  expiresAt: string
}

export interface AccountDeleteResult {
  status: 'frozen'
  frozenAt: string
  message: string
}

/** account.delete 桥错误码。 */
export type AccountDeleteErrorCode =
  | 'network_error'
  | 'server_error'
  | 'auth_lost'
  | 'invalid_token'
  | 'deletion_pending'
  | 'invalid_input'
  | 'unavailable'

// ─── T-M5-05c: 应用内反馈类型 ───

export type FeedbackCategory = 'bug' | 'suggestion' | 'other'

export interface FeedbackSubmitParams {
  category: FeedbackCategory
  content: string
  contact?: string
}

export interface FeedbackSubmitResult {
  id: string
}

/** feedback.submit 桥错误码。 */
export type FeedbackSubmitErrorCode =
  | 'auth_lost'
  | 'rate_limited'
  | 'invalid_input'
  | 'network_error'
  | 'server_error'
  | 'unavailable'
