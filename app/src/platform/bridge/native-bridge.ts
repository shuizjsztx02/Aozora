/**
 * Native Bridge — TypeScript 客户端，与 Kotlin AozoraBridge 通信。
 *
 * 协议：
 *   调用：window.AozoraBridge.call(version, requestId, command, payloadJson)
 *   回调：全局 __aozoraBridgeRespond(responseJson) 被 Kotlin 调用
 *
 * 设计：
 *   - Promise-based API
 *   - 请求 ID 唯一性保证
 *   - 超时处理（默认 10s）
 *   - 版本协商
 */

import {
  BRIDGE_VERSION,
  BridgeError,
  type BridgeResponse,
  type TransactionInput,
  type TransactionData,
  type BookInput,
  type BookData,
  type CategoryData,
  type AccountData,
  type BudgetData,
  type PendingMutationData,
} from './bridge-types'

declare global {
  interface Window {
    AozoraBridge?: {
      call(version: number, requestId: string, command: string, payload: string): void
      getBridgeVersion(): number
      ping(): string
    }
  }
}

/** 生成唯一请求 ID */
function generateRequestId(): string {
  return `req_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
}

/** 默认超时（ms） */
const DEFAULT_TIMEOUT = 10_000

/**
 * NativeBridge — 与 Kotlin 侧 AozoraBridge 通信的 Promise-based 客户端。
 */
export class NativeBridge {
  private pending = new Map<string, {
    resolve: (value: unknown) => void
    reject: (error: BridgeError) => void
    timer: ReturnType<typeof setTimeout>
  }>()
  private version: number

  constructor() {
    this.version = BRIDGE_VERSION
    // 注册全局回调
    if (typeof window !== 'undefined') {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ;(window as any).__aozoraBridgeRespond = (responseJson: string) => {
        this.handleResponse(responseJson)
      }
    }
  }

  /** 检查原生桥是否可用 */
  isAvailable(): boolean {
    return typeof window !== 'undefined' && window.AozoraBridge !== undefined
  }

  /** 底层调用 */
  call<T>(command: string, payload?: Record<string, unknown>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      if (!this.isAvailable()) {
        reject(new BridgeError('bridge_unavailable', 'Native bridge not available', ''))
        return
      }

      const requestId = generateRequestId()
      const timer = setTimeout(() => {
        this.pending.delete(requestId)
        reject(new BridgeError('timeout', `Command "${command}" timed out`, requestId))
      }, DEFAULT_TIMEOUT)

      this.pending.set(requestId, {
        resolve: resolve as (value: unknown) => void,
        reject,
        timer,
      })

      try {
        const payloadJson = payload ? JSON.stringify(payload) : '{}'
        window.AozoraBridge!.call(this.version, requestId, command, payloadJson)
      } catch (error) {
        clearTimeout(timer)
        this.pending.delete(requestId)
        reject(new BridgeError('bridge_call_failed', error instanceof Error ? error.message : String(error), requestId))
      }
    })
  }

  /** 处理来自 Kotlin 的响应 */
  private handleResponse(responseJson: string): void {
    let response: BridgeResponse
    try {
      response = JSON.parse(responseJson) as BridgeResponse
    } catch {
      return // 无法解析的响应，忽略
    }

    if (!response || typeof response !== 'object' || Array.isArray(response) ||
        typeof response.requestId !== 'string' || typeof response.success !== 'boolean') return

    const entry = this.pending.get(response.requestId)
    if (!entry) return

    clearTimeout(entry.timer)
    this.pending.delete(response.requestId)

    if (response.success) {
      entry.resolve(response.data)
    } else {
      entry.reject(
        new BridgeError(
          response.error?.code ?? 'unknown',
          response.error?.message ?? 'Unknown error',
          response.requestId
        )
      )
    }
  }

  // ─── 版本 ───

  async getVersion(): Promise<number> {
    const data = await this.call<{ version: number }>('bridge.getVersion')
    return data.version
  }

  // ─── Transaction ───

  async insertTransaction(tx: TransactionInput, mutationId: string): Promise<{ id: string; version: number }> {
    return this.call('transaction.insert', { tx, mutationId })
  }

  async getTransaction(id: string): Promise<TransactionData | null> {
    return this.call<TransactionData | null>('transaction.get', { id })
  }

  async listTransactionsByBook(bookId: string): Promise<TransactionData[]> {
    return this.call<TransactionData[]>('transaction.listByBook', { bookId })
  }

  async listTransactionsByDate(bizDateStart: string, bizDateEnd: string): Promise<TransactionData[]> {
    return this.call<TransactionData[]>('transaction.listByDate', { bizDateStart, bizDateEnd })
  }

  async softDeleteTransaction(id: string, mutationId: string): Promise<{ success: boolean }> {
    return this.call('transaction.softDelete', { id, mutationId })
  }

  // ─── Book ───

  async listBooks(): Promise<BookData[]> {
    return this.call<BookData[]>('book.list')
  }

  async insertBook(book: BookInput, mutationId: string): Promise<{ id: string }> {
    return this.call('book.insert', { book, mutationId })
  }

  // ─── Category ───

  async listCategories(): Promise<CategoryData[]> {
    return this.call<CategoryData[]>('category.list')
  }

  // ─── Account ───

  async listAccounts(): Promise<AccountData[]> {
    return this.call<AccountData[]>('account.list')
  }

  // R-M1-43 修复：补齐 Account 写命令
  async insertAccount(account: { id: string; ownerId: string; name: string; icon?: string; version: number }, mutationId: string): Promise<{ id: string }> {
    return this.call('account.insert', { account, mutationId })
  }

  async archiveAccount(id: string, mutationId: string): Promise<{ success: boolean }> {
    return this.call('account.archive', { id, mutationId })
  }

  // ─── Budget ───

  async listBudgets(bookId: string): Promise<BudgetData[]> {
    return this.call<BudgetData[]>('budget.list', { bookId })
  }

  // R-M1-43 修复：补齐 Budget 写命令
  async insertBudget(budget: { id: string; bookId: string; budgetType: string; categoryId?: string | null; period: string; amountCents: number; version: number }, mutationId: string): Promise<{ id: string }> {
    return this.call('budget.insert', { budget, mutationId })
  }

  async updateBudget(budget: { id: string; amountCents: number; version: number }, mutationId: string): Promise<{ id: string }> {
    return this.call('budget.update', { budget, mutationId })
  }

  async deleteBudget(id: string, mutationId: string): Promise<{ success: boolean }> {
    return this.call('budget.delete', { id, mutationId })
  }

  // ─── Pending Mutations ───

  async listPendingMutations(status?: string): Promise<PendingMutationData[]> {
    return this.call<PendingMutationData[]>('pendingMutation.list', status ? { status } : {})
  }

  async markPendingSyncing(mutationId: string): Promise<{ success: boolean }> {
    return this.call('pendingMutation.markSyncing', { mutationId })
  }

  async markPendingFailed(mutationId: string): Promise<{ success: boolean }> {
    return this.call('pendingMutation.markFailed', { mutationId })
  }

  async removePendingMutation(mutationId: string): Promise<{ success: boolean }> {
    return this.call('pendingMutation.remove', { mutationId })
  }

  // ─── 测试辅助 ───

  /** 获取当前 pending 请求数（用于测试） */
  pendingCount(): number {
    return this.pending.size
  }
}
