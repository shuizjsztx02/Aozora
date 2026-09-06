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
  type SyncStatusData,
  type SyncEvent,
  type ReminderStatusData,
  type AiReportResponse,
  type AiInterpretResponse,
  type AiValidateResponse,
  type AiExecuteResponse,
  type PermissionStatusData,
  type PermissionType,
  type PermissionResultEvent,
  type ImagePickResult,
  type ImageProgressEvent,
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

/** AI 命令超时（ms），与 ADR-003 的 25s SLO 对齐 */
const AI_TIMEOUT = 25_000

/** per-command 调用选项 */
export interface CallOptions {
  timeoutMs?: number
}

/**
 * NativeBridge — 与 Kotlin 侧 AozoraBridge 通信的 Promise-based 客户端。
 */
export class NativeBridge {
  private pending = new Map<string, {
    resolve: (value: unknown) => void
    reject: (error: BridgeError) => void
    timer?: ReturnType<typeof setTimeout>
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
  call<T>(command: string, payload?: Record<string, unknown>, options?: CallOptions): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      if (!this.isAvailable()) {
        reject(new BridgeError('bridge_unavailable', 'Native bridge not available', ''))
        return
      }

      const requestId = generateRequestId()
      const timeout = options?.timeoutMs ?? DEFAULT_TIMEOUT
      // timeoutMs === 0 表示不设超时（如 pickImage：用户可能在相机中停留很久）
      const timer = timeout > 0
        ? setTimeout(() => {
            this.pending.delete(requestId)
            reject(new BridgeError('timeout', `Command "${command}" timed out`, requestId))
          }, timeout)
        : undefined

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

  // ─── T-M3-02: Sync Engine ───

  /** 查询当前同步状态。 */
  async getSyncStatus(): Promise<SyncStatusData> {
    return this.call<SyncStatusData>('sync.getStatus')
  }

  /** 触发一轮同步（幂等；多次调用仅保留一个 worker）。 */
  async triggerSync(): Promise<{ enqueued: boolean }> {
    return this.call('sync.trigger')
  }

  /**
   * 注册同步事件监听（原生 → Web 推送）。
   * 返回取消订阅函数。
   *
   * 原生侧通过 `window.__aozoraSyncEvent__(event)` 调用；本方法只负责订阅分发。
   */
  onSyncEvent(listener: (event: SyncEvent) => void): () => void {
    if (typeof window === 'undefined') return () => {}
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const prev = (window as any).__aozoraSyncEvent__
    const wrapped = (event: SyncEvent) => {
      listener(event)
      if (typeof prev === 'function') prev(event)
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(window as any).__aozoraSyncEvent__ = wrapped
    return () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if ((window as any).__aozoraSyncEvent__ === wrapped) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ;(window as any).__aozoraSyncEvent__ = prev
      }
    }
  }

  // ─── T-M4-05a: 提醒调度 ───

  /** 查询提醒设置与通知权限状态。 */
  async getReminderStatus(): Promise<ReminderStatusData> {
    return this.call<ReminderStatusData>('reminder.getStatus')
  }

  /** 设置或取消每日提醒。 */
  async setReminder(enabled: boolean, hour: number, minute: number): Promise<{ success: boolean }> {
    return this.call('reminder.set', { enabled, hour, minute })
  }

  /** 检查通知权限是否已授予。 */
  async checkReminderPermission(): Promise<{ granted: boolean }> {
    return this.call('reminder.checkPermission')
  }

  // ─── T-M4-03c: AI 洞察 ───

  /** 请求 AI 月度报告（ADR-003 通道，25s 超时）。 */
  async reportAI(bookId: string, period: string): Promise<AiReportResponse> {
    return this.call<AiReportResponse>('ai.report', { bookId, period }, { timeoutMs: AI_TIMEOUT })
  }

  // ─── T-M4-02c: AI 助手对话 ───

  /** AI 解读自然语言输入（ADR-003 通道，25s 超时）。 */
  async interpretAI(input: string): Promise<AiInterpretResponse> {
    return this.call<AiInterpretResponse>('ai.interpret', { input }, { timeoutMs: AI_TIMEOUT })
  }

  /** AI 验证 ActionProposal（ADR-003 通道，25s 超时）。 */
  async validateProposal(proposal: Record<string, unknown>, bookId: string): Promise<AiValidateResponse> {
    return this.call<AiValidateResponse>('ai.validate', { proposal, book_id: bookId }, { timeoutMs: AI_TIMEOUT })
  }

  /** AI 执行已验证的 ActionProposal（ADR-003 通道，25s 超时）。 */
  async executeProposal(proposal: Record<string, unknown>, bookId: string, confirmationToken: string): Promise<AiExecuteResponse> {
    return this.call<AiExecuteResponse>('ai.execute', { proposal, book_id: bookId, confirmation_token: confirmationToken }, { timeoutMs: AI_TIMEOUT })
  }

  // ─── T-M4-04a: 权限管理 ───

  /** 查询所有 T-M4-04 相关权限状态。 */
  async getPermissionStatus(): Promise<PermissionStatusData> {
    return this.call<PermissionStatusData>('permissions.getStatus')
  }

  /**
   * 请求单个权限。
   *
   * 立即返回 { pending: true }，实际结果通过 onPermissionResult 监听。
   */
  async requestPermission(permission: PermissionType): Promise<{ pending: boolean }> {
    return this.call('permissions.request', { permission })
  }

  /** 打开应用设置页（用于永久拒绝后的引导）。 */
  async openPermissionSettings(): Promise<{ success: boolean }> {
    return this.call('permissions.openSettings')
  }

  /**
   * 注册权限结果监听（Android 权限对话框关闭后的异步推送）。
   * 返回取消订阅函数。
   */
  onPermissionResult(listener: (event: PermissionResultEvent) => void): () => void {
    if (typeof window === 'undefined') return () => {}
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const prev = (window as any).__aozoraPermissionResult__
    const wrapped = (eventJson: string) => {
      try {
        const event = typeof eventJson === 'string' ? JSON.parse(eventJson) : eventJson
        listener(event as PermissionResultEvent)
      } catch { /* ignore parse errors */ }
      if (typeof prev === 'function') prev(eventJson)
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(window as any).__aozoraPermissionResult__ = wrapped
    return () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if ((window as any).__aozoraPermissionResult__ === wrapped) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ;(window as any).__aozoraPermissionResult__ = prev
      }
    }
  }

  // ─── T-M4-04d: 图片链路 ───

  /**
   * 打开系统图片选择器（相机或相册），压缩并上传到服务端。
   *
   * 整个流程在 Android 侧完成：
   * 1. 打开系统相机/相册 Activity（bridge 请求挂起等待用户返回）
   * 2. 客户端压缩为 JPEG（质量由 quality 参数控制）
   * 3. 上传到 POST /attachments/upload（Bearer token 由 Kotlin 注入）
   * 4. 上传进度通过 onImageProgress 推送
   * 5. 返回服务端生成的 attachmentId
   *
   * 超时：无固定超时（用户可能在相机中停留很久），前端自行管理 UI 超时。
   */
  async pickImage(source: 'camera' | 'gallery', quality = 80): Promise<ImagePickResult> {
    // 不设固定超时——用户可能在相机/相册中停留很久
    return this.call<ImagePickResult>('image.pick', { source, quality }, { timeoutMs: 0 })
  }

  /** 删除服务端附件（用于清理用户放弃的草稿）。 */
  async deleteImage(attachmentId: string): Promise<{ success: boolean }> {
    return this.call('image.delete', { attachmentId })
  }

  /**
   * 注册上传进度监听（Android 压缩上传过程中通过 __aozoraImageProgress__ 推送）。
   * 返回取消订阅函数。
   */
  onImageProgress(listener: (event: ImageProgressEvent) => void): () => void {
    if (typeof window === 'undefined') return () => {}
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const prev = (window as any).__aozoraImageProgress__
    const wrapped = (event: ImageProgressEvent) => {
      listener(event)
      if (typeof prev === 'function') prev(event)
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(window as any).__aozoraImageProgress__ = wrapped
    return () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if ((window as any).__aozoraImageProgress__ === wrapped) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ;(window as any).__aozoraImageProgress__ = prev
      }
    }
  }

  // ─── T-M4-04b: 语音识别 ───

  /**
   * 开始语音录音和实时转写。
   *
   * 录音过程中通过 onVoicePartial 推送实时部分转写结果；
   * 停止录音后通过 onVoiceFinal 推送最终转写文本。
   *
   * @returns { started: true } 表示录音已启动
   */
  async startVoiceRecording(): Promise<{ started: boolean }> {
    return this.call('voice.startRecording')
  }

  /**
   * 停止语音录音并返回最终转写结果。
   *
   * @returns { stopped: true, text: string } 最终转写文本
   */
  async stopVoiceRecording(): Promise<{ stopped: boolean; text: string }> {
    return this.call('voice.stopRecording')
  }

  /**
   * 注册部分转写结果监听（Android 录音过程中通过 __aozoraVoicePartial__ 推送）。
   * 返回取消订阅函数。
   */
  onVoicePartial(listener: (text: string) => void): () => void {
    if (typeof window === 'undefined') return () => {}
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const prev = (window as any).__aozoraVoicePartial__
    const wrapped = (text: string) => {
      listener(text)
      if (typeof prev === 'function') prev(text)
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(window as any).__aozoraVoicePartial__ = wrapped
    return () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if ((window as any).__aozoraVoicePartial__ === wrapped) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ;(window as any).__aozoraVoicePartial__ = prev
      }
    }
  }

  /**
   * 注册最终转写结果监听（Android 停止录音后通过 __aozoraVoiceFinal__ 推送）。
   * 返回取消订阅函数。
   */
  onVoiceFinal(listener: (text: string) => void): () => void {
    if (typeof window === 'undefined') return () => {}
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const prev = (window as any).__aozoraVoiceFinal__
    const wrapped = (text: string) => {
      listener(text)
      if (typeof prev === 'function') prev(text)
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(window as any).__aozoraVoiceFinal__ = wrapped
    return () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if ((window as any).__aozoraVoiceFinal__ === wrapped) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ;(window as any).__aozoraVoiceFinal__ = prev
      }
    }
  }

  /**
   * 注册语音识别错误监听（Android 通过 __aozoraVoiceError__ 推送）。
   * 返回取消订阅函数。
   *
   * Bug-B4 fix：Android 现在传裸对象字面量 {code, message}，与 __aozoraImageProgress__ 一致，
   * 不再需要 JSON.parse 字符串。保留 typeof === 'string' 分支以兼容旧版 Android。
   */
  onVoiceError(listener: (error: { code: string; message: string }) => void): () => void {
    if (typeof window === 'undefined') return () => {}
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const prev = (window as any).__aozoraVoiceError__
    const wrapped = (errorOrJson: { code: string; message: string } | string) => {
      try {
        const error = typeof errorOrJson === 'string' ? JSON.parse(errorOrJson) : errorOrJson
        listener(error as { code: string; message: string })
      } catch { /* ignore parse errors */ }
      if (typeof prev === 'function') prev(errorOrJson)
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(window as any).__aozoraVoiceError__ = wrapped
    return () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if ((window as any).__aozoraVoiceError__ === wrapped) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ;(window as any).__aozoraVoiceError__ = prev
      }
    }
  }

  // ─── 测试辅助 ───

  /** 获取当前 pending 请求数（用于测试） */
  pendingCount(): number {
    return this.pending.size
  }
}
