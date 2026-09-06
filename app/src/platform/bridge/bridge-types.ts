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

  // Book
  BOOK_LIST: 'book.list',
  BOOK_INSERT: 'book.insert',

  // Category
  CATEGORY_LIST: 'category.list',

  // Account
  ACCOUNT_LIST: 'account.list',
  ACCOUNT_INSERT: 'account.insert',
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

  // T-M4-04a: 权限管理
  PERMISSIONS_GET_STATUS: 'permissions.getStatus',
  PERMISSIONS_REQUEST: 'permissions.request',
  PERMISSIONS_OPEN_SETTINGS: 'permissions.openSettings',

  // T-M4-04b 占位：语音
  VOICE_START_RECORDING: 'voice.startRecording',
  VOICE_STOP_RECORDING: 'voice.stopRecording',

  // T-M4-04d 占位：图片
  IMAGE_PICK: 'image.pick',
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
  source: 'manual' | 'nlu_local' | 'nlu_ai' | 'import'
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
  source: 'manual' | 'nlu_local' | 'nlu_ai' | 'import'
  version?: number
}

export interface BookData {
  id: string
  ownerId: string
  name: string
  icon: string
  version: number
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
  version: number
}

export interface AccountData {
  id: string
  name: string
  icon: string
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

export type PermissionType = 'RECORD_AUDIO' | 'CAMERA' | 'READ_MEDIA_IMAGES'

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
