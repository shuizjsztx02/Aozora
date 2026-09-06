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
