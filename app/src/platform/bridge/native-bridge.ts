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
  type CategoryInput,
  type IconPickResult,
  type ThemePickBackgroundResult,
  type SavingsGoalData,
  type SavingsGoalInput,
  type ScheduledRuleData,
  type ScheduledRuleInput,
  type PersonaData,
  type PersonaInput,
  type AccountData,
  type AccountInput,
  type BudgetData,
  type PendingMutationData,
  type SyncStatusData,
  type SyncEvent,
  type ReminderStatusData,
  type AiReportResponse,
  type AiInterpretContext,
  type AiInterpretResponse,
  type AiValidateResponse,
  type AiExecuteResponse,
  type AiAgentAck,
  type AiApprovalResultAck,
  type AiApprovalResultParams,
  type AiStreamEvent,
  type AiConversationSummary,
  type AiConversationListResponse,
  type AiConversationEventsResponse,
  type PermissionStatusData,
  type PermissionType,
  type PermissionResultEvent,
  type ImagePickResult,
  type ImageProgressEvent,
  type ParseImageResult,
  type ConsentRecordParams,
  type ConsentRecordResult,
  type ConsentStatusResult,
  type ExportCreateParams,
  type ExportData,
  type ExportDownloadResult,
  type AccountDeletionTokenResult,
  type AccountDeleteResult,
  type FeedbackSubmitParams,
  type FeedbackSubmitResult,
  type AuthLoginResult,
  type MeData,
  type DeviceExecuteResult,
  type DeviceOutcomeStatus,
  type DeviceResultAck,
  type AutoBookStatusData,
  type AutoBookCaptureData,
  type AutoBookCaptureStatus,
  type AutoBookResolution,
  type AutoBookCaptureOverrides,
  type AutoBookResolveResult,
} from './bridge-types'
// T-M7-03d 收口：`ai.agent` 的 `conversation_id` 只认服务端 id，而会话桶的键空间
// （本地会话 `local:*` / `__local__` vs 服务端会话）由 store 定义——单一事实源。
import { toServerConversationId } from '../store/assistant-chat-store'

// Re-export BridgeError for consumers (e.g. MePage)
export { BridgeError }

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
    // 注册全局回调。
    // 多个实例共存时（如组件内直接 new 而非 useBridge 单例）链式调用，
    // 响应分发给所有实例、由持有该 requestId 的实例处理——
    // 避免后注册的实例覆写回调导致先发起的请求响应被丢弃、Promise 挂到超时。
    if (typeof window !== 'undefined') {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const prev = (window as any).__aozoraBridgeRespond
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ;(window as any).__aozoraBridgeRespond = (responseJson: string) => {
        this.handleResponse(responseJson)
        if (typeof prev === 'function') prev(responseJson)
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

  /**
   * 第十二轮 T12-7（REQ-36）：交易编辑真 update（保 id 保附件关联，决策 #4）。
   * tx 为全字段且 version 必填（= 当前版本 +1，Kotlin 侧乐观锁）；payload 形状
   * { tx, mutationId } 与 transaction.insert 一致。
   */
  async updateTransaction(
    tx: TransactionInput & { version: number },
    mutationId: string,
  ): Promise<{ id: string; version: number }> {
    return this.call('transaction.update', { tx, mutationId })
  }

  // ─── Book ───

  async listBooks(): Promise<BookData[]> {
    return this.call<BookData[]>('book.list')
  }

  async insertBook(book: BookInput, mutationId: string): Promise<{ id: string }> {
    return this.call('book.insert', { book, mutationId })
  }

  /**
   * 第十四轮 T14-2（REQ-39 / 决策 #3）：账本编辑（形状照 updateAccount）。
   * book.update 局部合并，version 必填且必须大于当前值（Kotlin 侧乐观锁）。
   */
  async updateBook(book: BookInput & { version: number }, mutationId: string): Promise<{ id: string }> {
    return this.call('book.update', { book, mutationId })
  }

  // ─── Category ───

  async listCategories(): Promise<CategoryData[]> {
    return this.call<CategoryData[]>('category.list')
  }

  // 第八轮 REQ-8：分类写命令走包裹结构 {category, mutationId}，与 native 契约一致
  async insertCategory(category: CategoryInput, mutationId: string): Promise<{ id: string }> {
    return this.call('category.insert', { category, mutationId })
  }

  async updateCategory(category: CategoryInput, mutationId: string): Promise<{ id: string }> {
    return this.call('category.update', { category, mutationId })
  }

  /**
   * 分类照片图标选择（第八轮 REQ-8）。打开原生图选取 128px 缩略图，
   * 返回 base64 data URI（无服务端上传，离线可用）。取消 reject code='cancelled'。
   */
  async pickIcon(source: 'camera' | 'gallery'): Promise<IconPickResult> {
    return this.call<IconPickResult>('icon.pick', { source }, { timeoutMs: 0 })
  }

  /**
   * 主题自定义背景选图（第十六轮 T16-5 / REQ-61）。打开原生图选取全屏背景图
   * （1080px/q70，独立于 pickIcon 的 128px/48KB），返回 base64 data URI；
   * 无服务端上传，结果由调用方落 IndexedDB。取消 reject code='cancelled'。
   */
  async pickThemeBackground(source: 'camera' | 'gallery' = 'gallery'): Promise<ThemePickBackgroundResult> {
    return this.call<ThemePickBackgroundResult>('theme.pickBackground', { source }, { timeoutMs: 0 })
  }

  // ─── Savings Goals（第八轮 REQ-7，仅本地） ───

  async listSavingsGoals(): Promise<SavingsGoalData[]> {
    return this.call<SavingsGoalData[]>('savings.list')
  }

  async insertSavingsGoal(goal: SavingsGoalInput): Promise<{ id: string }> {
    return this.call('savings.insert', { goal })
  }

  async updateSavingsGoal(goal: SavingsGoalInput): Promise<{ id: string }> {
    return this.call('savings.update', { goal })
  }

  async archiveSavingsGoal(id: string): Promise<{ success: boolean }> {
    return this.call('savings.archive', { id })
  }

  // ─── Scheduled Rules（第十一轮 REQ-26，仅本地，镜像 savings 无 mutationId） ───

  async listScheduledRules(): Promise<ScheduledRuleData[]> {
    return this.call<ScheduledRuleData[]>('scheduled.list')
  }

  async insertScheduledRule(rule: ScheduledRuleInput): Promise<{ id: string }> {
    return this.call('scheduled.insert', { rule })
  }

  /** 局部合并：键缺省保留原值；accountId/endDate 显式 null = 清除；version 必须大于当前值。 */
  async updateScheduledRule(rule: ScheduledRuleInput): Promise<{ id: string }> {
    return this.call('scheduled.update', { rule })
  }

  async archiveScheduledRule(id: string): Promise<{ success: boolean }> {
    return this.call('scheduled.archive', { id })
  }

  // ─── Chat Personas（第十一轮 REQ-28，仅本地，镜像 scheduled 无 mutationId） ───

  async listPersonas(): Promise<PersonaData[]> {
    return this.call<PersonaData[]>('persona.list')
  }

  async insertPersona(persona: PersonaInput): Promise<{ id: string }> {
    return this.call('persona.insert', { persona })
  }

  /** 局部合并：键缺省保留原值；avatar 显式 null = 清除；version 必须大于当前值。 */
  async updatePersona(persona: PersonaInput): Promise<{ id: string }> {
    return this.call('persona.update', { persona })
  }

  async archivePersona(id: string): Promise<{ success: boolean }> {
    return this.call('persona.archive', { id })
  }

  // ─── Account ───

  async listAccounts(): Promise<AccountData[]> {
    return this.call<AccountData[]>('account.list')
  }

  // R-M1-43 修复：补齐 Account 写命令；T7-2（REQ-5）参数收敛为 AccountInput
  async insertAccount(account: AccountInput, mutationId: string): Promise<{ id: string }> {
    return this.call('account.insert', { account, mutationId })
  }

  /** T7-2（REQ-5/决策 #4）：局部合并更新，version 必须大于当前值。 */
  async updateAccount(account: AccountInput, mutationId: string): Promise<{ id: string }> {
    return this.call('account.update', { account, mutationId })
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

  /** T11-1（REQ-23 编辑）：budgetType/period 可选传入（成对改；native 局部合并，缺省沿用原值）。 */
  async updateBudget(budget: { id: string; amountCents: number; version: number; budgetType?: 'monthly' | 'annual'; period?: string; categoryId?: string | null }, mutationId: string): Promise<{ id: string }> {
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

  /** AI 解读自然语言输入（ADR-003 通道，25s 超时）。
   *  T11-6（决策 #7）：context = 画像/人设注入（{persona, profile}）；null/缺省不带键，
   *  服务端行为与旧版一致。本地 NLU 路径不消费 context。 */
  async interpretAI(input: string, context?: AiInterpretContext | null): Promise<AiInterpretResponse> {
    return this.call<AiInterpretResponse>(
      'ai.interpret',
      context ? { input, context } : { input },
      { timeoutMs: AI_TIMEOUT },
    )
  }

  /** AI 验证 ActionProposal（ADR-003 通道，25s 超时）。 */
  async validateProposal(proposal: Record<string, unknown>, bookId: string): Promise<AiValidateResponse> {
    return this.call<AiValidateResponse>('ai.validate', { proposal, book_id: bookId }, { timeoutMs: AI_TIMEOUT })
  }

  /** AI 执行已验证的 ActionProposal（ADR-003 通道，25s 超时）。 */
  async executeProposal(proposal: Record<string, unknown>, bookId: string, confirmationToken: string): Promise<AiExecuteResponse> {
    return this.call<AiExecuteResponse>('ai.execute', { proposal, book_id: bookId, confirmation_token: confirmationToken }, { timeoutMs: AI_TIMEOUT })
  }

  // ─── T-M7-02h: agent 流式通道 ───

  /**
   * 启动一轮 agent 回合（ADR-003 通道）。
   *
   * **立即返回** `{pending, streamId}`——服务端事件经 `onAiStream` 推送
   * （「命令返回 pending + 事件推送结果」，同 `requestPermission`，架构 §8.2）。
   * 事件是流式的，这里**不设 25 s 单发超时**：一轮 loop 由多个步骤构成，
   * 时长上限由服务端步骤边界与用户的 `cancelAgent()` 兜底（架构 §8.3）。
   *
   * @param conversationId 上一轮 `done.conversation_id`；首轮省略。
   *
   * T-M7-03d 收口：**只下发服务端 id**——本地键（`local:*` / `__local__`）一律省略，
   * 服务端据此开新会话。这条闸放在**请求组装处**（同 Kotlin 的 `buildAgentRequest`），
   * 任何调用方都不可能绕过：会话桶的键空间属 `assistant-chat-store`，那边的「本地会话」
   * 与「服务端会话」是两种东西，而 `/ai/agent` 只认后者（发上去会撞服务端的 UUID
   * 校验，得到 422 而不是一条新会话）。
   */
  async runAgent(input: string, bookId: string, conversationId?: string | null): Promise<AiAgentAck> {
    const payload: Record<string, unknown> = { input, book_id: bookId }
    const serverId = toServerConversationId(conversationId ?? null)
    if (serverId) payload.conversation_id = serverId
    return this.call<AiAgentAck>('ai.agent', payload)
  }

  /**
   * 取消当前 agent 流。服务端把断连当取消（没有单独的取消端点），
   * 因此这里的动作就是关掉 SSE 长连接；无活动流时返回 `{cancelled: false}`。
   */
  async cancelAgent(): Promise<{ cancelled: boolean }> {
    return this.call('ai.cancel')
  }

  /**
   * M8-02b：把确认卡的终态处置上报给服务端（四值）。
   *
   * `allowed-once` 由 `/ai/execute` 在服务端留档，这条上报是冗余但被服务端忽略；
   * 其余三值只有客户端知道。失败**不上抛**——审计留档失败不该改变用户看到的
   * 结果（服务端同一口径：`_record_failure` 尽力而为）。
   */
  async reportApprovalResult(params: AiApprovalResultParams): Promise<AiApprovalResultAck> {
    const payload: Record<string, unknown> = {
      proposalId: params.proposalId,
      conversationId: params.conversationId,
      result: params.result,
    }
    if (params.reason) payload.reason = params.reason
    return this.call<AiApprovalResultAck>('ai.approval_result', payload)
  }

  /**
   * 注册 agent 流事件监听（原生 → Web 推送）。返回取消订阅函数。
   *
   * 原生侧通过 `window.__aozoraAiStream__` 调用；事件带 `streamId`
   * （= `runAgent` 那次调用的 requestId），多实例/多流时据此归属。
   */
  onAiStream(listener: (event: AiStreamEvent) => void): () => void {
    if (typeof window === 'undefined') return () => {}
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const prev = (window as any).__aozoraAiStream__
    const wrapped = (eventJson: string) => {
      try {
        const event = typeof eventJson === 'string' ? JSON.parse(eventJson) : eventJson
        listener(event as AiStreamEvent)
      } catch { /* ignore parse errors */ }
      if (typeof prev === 'function') prev(eventJson)
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(window as any).__aozoraAiStream__ = wrapped
    return () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if ((window as any).__aozoraAiStream__ === wrapped) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ;(window as any).__aozoraAiStream__ = prev
      }
    }
  }

  // ─── T-M7-05f: 设备能力通道（客户端执行器） ───

  /**
   * 执行一个设备动作（T-M7-05e 的 `device_request` 帧的落地侧）。
   *
   * 能力名 → 桥命令的分派与结局归一化都在原生侧（`AozoraBridge.handleDeviceExecute`），
   * 这一层因此只面对四个结局值，不认识任何一个具体能力的错误码族。
   *
   * **不设调用超时**（`timeoutMs: 0`）：需结果类的等待时长由用户在设备上决定
   * （权限对话框 / 相册 / 相机），服务端自己有 `timeout_seconds` 的挂起上限；桥这
   * 一层再加一个更短的超时只会把「用户还在选」误判成失败（同 `pickImage` 的先例）。
   */
  async executeDevice(
    capability: string, payload: Record<string, unknown>,
  ): Promise<DeviceExecuteResult> {
    return this.call<DeviceExecuteResult>(
      'device.execute', { capability, payload }, { timeoutMs: 0 },
    )
  }

  /**
   * 回传需结果类的结局（`POST /ai/device-result`，唤醒挂起中的那一轮 loop）。
   *
   * **404 等价于成功**：服务端对「没有这条请求 / 已经回过 / 已经超时注销 / 属于别人」
   * 一律回同一个 404，桥层把它翻成 `{accepted: true}`——那一轮的挂起条目已经不在，
   * 重投只会得到同一个答案（与 `deleteConversation` 的幂等口径同源）。
   */
  async resultDevice(
    requestId: string, status: DeviceOutcomeStatus, data?: Record<string, unknown>,
  ): Promise<DeviceResultAck> {
    const payload: Record<string, unknown> = { request_id: requestId, status }
    if (data) payload.data = data
    return this.call<DeviceResultAck>('device.result', payload)
  }

  // ─── T-M7-03d: 会话索引 ───

  /**
   * 本人的会话索引（服务端按活跃时间倒序；列表不分页，03a 口径）。
   *
   * 失败码：`auth_lost`（未登录 / 登录失效）、`network`、`timeout`、`429`、`503`、
   * `unavailable`（桥未注入 AI 客户端）。**未登录与断网都走这里**——调用方据此
   * 降级为「只读本地投影缓存」。
   */
  async listConversations(): Promise<AiConversationListResponse> {
    return this.call<AiConversationListResponse>('conversation.list')
  }

  /** 重命名会话（标题 1–120 字，服务端先 strip）。404 → `code === 'not_found'`。 */
  async renameConversation(id: string, title: string): Promise<AiConversationSummary> {
    return this.call<AiConversationSummary>('conversation.rename', { id, title })
  }

  /**
   * 删除会话（服务端 204）。
   *
   * **重试安全**：已删除 / 别人的 / 不存在的 id 一律 404 `not_found`——调用方应把它
   * 与成功同等对待（「这个 id 不是我的会话了」），不要弹错误。
   */
  async deleteConversation(id: string): Promise<{ success: boolean }> {
    return this.call<{ success: boolean }>('conversation.delete', { id })
  }

  /**
   * 读会话事件（T-M7-04b）——**断连重连的续读入口**。
   *
   * `afterSeq` 是严格大于的起点：传上一批最后一条的 `seq` 就只取新落下的部分。
   * 省略（或 ≤ 0）= 从头拉全量——客户端还没有游标时的首次续读。404
   * `not_found` 与 `conversation.delete` 同口径：调用方按「这个 id 不是我的会话了」
   * 处理，不要弹错误。
   */
  async fetchConversationEvents(
    id: string, afterSeq?: number | null,
  ): Promise<AiConversationEventsResponse> {
    const payload: Record<string, unknown> = { id }
    if (afterSeq && afterSeq > 0) payload.after_seq = afterSeq
    return this.call<AiConversationEventsResponse>('conversation.events', payload)
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

  /**
   * 打开系统设置页。`target` 缺省跳应用详情页（保持既有行为），'accessibility' /
   * 'notification_listener' 跳对应的系统级页面（第二十一轮 T21-7 / REQ-83 决策 #10：
   * 两类授权都是「设置页授权」，不是运行时权限）。
   */
  async openPermissionSettings(
    target: 'app' | 'accessibility' | 'notification_listener' = 'app'
  ): Promise<{ success: boolean }> {
    return this.call('permissions.openSettings', { target })
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
   * T-M4-04e: 解析已上传的小票/账单图片。
   *
   * 调用服务端 /ai/parse-image，返回 ActionProposal（add_tx_batch）。
   * 超时设为 120s——视觉模型可能较慢。
   */
  async parseImage(attachmentId: string, bookId: string): Promise<ParseImageResult> {
    return this.call<ParseImageResult>(
      'image.parse',
      { attachmentId, bookId },
      { timeoutMs: 120_000 },
    )
  }

  /**
   * NEW-R6-2 fix: 取消正在进行的图片解析请求。
   *
   * Kotlin bridge 会取消底层 HTTP 调用，避免旧请求继续消耗 tokens。
   * 发送为 fire-and-forget——不等待响应，超时极短。
   */
  cancelImageParse(): void {
    this.call('image.parse_cancel', {}).catch(() => {
      // Cancel may arrive after parse already completed — ignore errors
    })
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

  // ─── T-M5-04a: 隐私同意 ───

  /**
   * 记录隐私政策同意。
   *
   * 写入 Room user_consent 表 + ConsentStore（SharedPreferences）。
   */
  async recordConsent(params?: ConsentRecordParams): Promise<ConsentRecordResult> {
    return this.call<ConsentRecordResult>('consent.record', {
      policyVersion: params?.policyVersion ?? 'v1',
      consentedAt: params?.consentedAt ?? Date.now(),
      ...(params?.deviceId && { deviceId: params.deviceId }),
    })
  }

  /** 查询本地隐私同意状态。 */
  async getConsentStatus(): Promise<ConsentStatusResult> {
    return this.call<ConsentStatusResult>('consent.status')
  }

  // ─── T-M5-04b: 数据导出 ───

  /**
   * 创建数据导出。服务端同步生成 JSON/CSV 文件。
   */
  async createExport(params: ExportCreateParams): Promise<ExportData> {
    return this.call<ExportData>('export.create', {
      bookId: params.bookId,
      format: params.format ?? 'json',
    })
  }

  /** 列出当前用户所有导出（按创建时间倒序）。 */
  async listExports(): Promise<ExportData[]> {
    return this.call<ExportData[]>('export.list')
  }

  /** 查询单个导出状态。 */
  async getExport(exportId: string): Promise<ExportData> {
    return this.call<ExportData>('export.get', { exportId })
  }

  /**
   * 下载导出文件到本地 Downloads/Aozora/ 目录。
   *
   * 返回本地路径 + 文件内容（≤ 20MB），前端用 content 做对账校验。
   */
  async downloadExport(exportId: string): Promise<ExportDownloadResult> {
    return this.call<ExportDownloadResult>('export.download', { exportId })
  }

  // ─── T-M5-04c: 账号删除 ───

  /**
   * 请求一次性 confirm_token。
   *
   * 返回的 token 必须在 5 分钟内通过 [deleteAccount] 消费，否则服务端拒绝。
   * 前端应在用户输入 "DELETE" 二次确认后调用 [deleteAccount]。
   */
  async requestAccountDeletionToken(): Promise<AccountDeletionTokenResult> {
    return this.call<AccountDeletionTokenResult>('account.requestDeletionToken')
  }

  /**
   * 消费 confirm_token 完成账号删除。
   *
   * 成功后：
   * - 服务端将账号置为 frozen（7 天后物理删除）
   * - 客户端清空 Room / TokenStore / ConsentStore / localStorage
   * - 跳回首启页
   */
  async deleteAccount(confirmToken: string): Promise<AccountDeleteResult> {
    return this.call<AccountDeleteResult>('account.delete', { confirmToken })
  }

  // ─── T-M5-05c: 应用内反馈 ───

  /**
   * 提交应用内反馈。
   *
   * @param params.category - "bug" | "suggestion" | "other"
   * @param params.content - 自由文本（1–4000 字符）
   * @param params.contact - 可选联系方式
   *
   * 错误码：
   * - rate_limited: 超出 5 次/小时限制
   * - auth_lost: 登录已失效
   * - network_error: 网络错误
   * - server_error: 服务端错误
   */
  async submitFeedback(params: FeedbackSubmitParams): Promise<FeedbackSubmitResult> {
    return this.call<FeedbackSubmitResult>('feedback.submit', { ...params })
  }

  // ─── FIX-002 / BUG-014: 触感反馈 ───

  /**
   * 触发设备震动反馈。
   *
   * @param kind - "success"（短震 50ms）或 "error"（长震 200ms）
   *
   * 非原生环境（如浏览器调试）下为 no-op。
   */
  async hapticFeedback(kind: 'success' | 'error' = 'success'): Promise<void> {
    try {
      await this.call('haptic.feedback', { kind })
    } catch {
      // 非原生环境下忽略
    }
  }

  // ─── FIX-003 / BUG-015: 系统分享 ───

  /**
   * 调起系统分享菜单，分享文本内容。
   *
   * 非原生环境下为 no-op。
   */
  async shareText(text: string, subject?: string): Promise<void> {
    try {
      await this.call('share.text', { text, subject })
    } catch {
      // 非原生环境或无分享目标 — 静默忽略
    }
  }

  // ─── FIX-004 / BUG-001: 认证状态 ───

  /** 查询当前认证状态（游客/已注册、userId、deviceId）。 */
  async getAuthStatus(): Promise<{ registered: boolean; userId: string | null; deviceId: string | null }> {
    return this.call('auth.getStatus')
  }

  /**
   * 同步阻塞跑完整一轮 push/pull（首次会自动 /auth/guest 注册）。
   * 与 triggerSync（仅入队 WorkManager）区分；调用方超时需 ≥ 同步预算。
   * 注：原名 triggerSync 与上方入队版重名（TS2393），第六轮合入时更名。
   */
  async triggerAuthSync(): Promise<{ outcome: string }> {
    return this.call('auth.triggerSync')
  }

  // ─── 第十轮 T10-3（REQ-19）：登录与安全 ───

  /**
   * 手机号 + 验证码登录（未注册号码自动注册）。
   * 成功后 native 已写 token（user 身份切换为手机号账号）；
   * 调用方须随后 triggerAuthSync()（新账号云端为空 → bootstrap 清本地重建）。
   * 错误码：invalid_input / invalid_code / server_rejected / auth_lost / network_error。
   */
  async loginAuth(phone: string, code: string): Promise<AuthLoginResult> {
    return this.call('auth.login', { phone, code })
  }

  /**
   * 登出：吊销本设备会话 + 清本地 token + 清全部本地业务数据（云端保留）。
   * 调用方成功后 navigate('/onboarding', { replace: true })。
   * 错误码：wipe_failed（本地数据清理失败，必须提示用户）。
   */
  async logoutAuth(): Promise<{ success: boolean }> {
    return this.call('auth.logout')
  }

  /**
   * GET /me 透传（id/nickname/phone/status/timezone…）。401 → auth_lost。
   *
   * T18-8（REQ-71）：`options.timeoutMs` 是给**可达性探测**用的短超时
   * （`platform/cloud-reachability.ts` 传 4s）——默认 10s 会把冷启动第一次输入
   * 拖到十秒。不给就是原行为。
   */
  async getMe(options?: CallOptions): Promise<MeData> {
    return this.call('auth.me', undefined, options)
  }

  /** PATCH /me 更新昵称；native 成功后镜像写 sync_meta display_name（离线可读）。 */
  async updateProfile(nickname: string): Promise<MeData> {
    return this.call('auth.updateProfile', { nickname })
  }

  // ─── T9-5a（REQ-11）：本地展示元数据（sync_meta 白名单键） ───

  /**
   * 批量读 meta。key 限 native 白名单（default_book_id / default_account_id /
   * book_order / account_order / avatar_uri / display_name / budget_carryover /
   * budget_carryover_mark / widget_config / user_profile），未设置的键返回 null。
   */
  async getMeta(keys: string[]): Promise<Record<string, string | null>> {
    const r = await this.call<{ values?: Record<string, string | null> }>('meta.get', { keys })
    return r.values ?? {}
  }

  /** 写单个 meta 键（native 校验白名单 + 分键上限：order/default 8KB、avatar_uri 64KB、display_name 64 字符、widget_config 4KB）。 */
  async setMeta(key: string, value: string): Promise<{ success: boolean }> {
    return this.call('meta.set', { key, value })
  }

  // ─── 第十一轮 T11-2（REQ-24 / 决策 #5）：桌面小组件 ───

  /** 手动触发桌面小组件刷新（配置保存后 / "立即刷新"按钮）。 */
  async refreshWidget(): Promise<{ success: boolean }> {
    return this.call('widget.refresh', {})
  }

  // ─── 第二十一轮 T21-5（REQ-83）：自动记账（autobook.* 四条） ───

  /** 自动记账总状态：开关 / 两类授权态 / 今日捕获数 / 待确认数。 */
  async getAutoBookStatus(): Promise<AutoBookStatusData> {
    return this.call<AutoBookStatusData>('autobook.get_status')
  }

  /** 写总开关（设备本地自管 meta 键，不进 meta 白名单、不上云）。 */
  async setAutoBookEnabled(enabled: boolean): Promise<{ success: boolean; enabled: boolean }> {
    return this.call('autobook.set_enabled', { enabled })
  }

  /** 待确认箱分页（默认 `status: 'pending_confirm'`；被 L2 合并的留痕行不返回）。 */
  async listAutoBookCaptures(opts?: {
    status?: AutoBookCaptureStatus
    limit?: number
    offset?: number
  }): Promise<AutoBookCaptureData[]> {
    const payload: Record<string, unknown> = {}
    if (opts?.status !== undefined) payload.status = opts.status
    if (opts?.limit !== undefined) payload.limit = opts.limit
    if (opts?.offset !== undefined) payload.offset = opts.offset
    return this.call<AutoBookCaptureData[]>('autobook.list_captures', payload)
  }

  /**
   * 处置一条待确认捕获：确认 / 改后确认 / 忽略。
   *
   * 错误码：`not_found`（captureId 不存在）、`invalid_input`（解析结果不足以落账，
   * 或缺字段 / 未知处置值）、`unavailable`（桥未注入自动记账库）。
   */
  async resolveAutoBookCapture(
    captureId: string,
    resolution: AutoBookResolution,
    overrides?: AutoBookCaptureOverrides,
  ): Promise<AutoBookResolveResult> {
    const payload: Record<string, unknown> = { captureId, resolution }
    if (overrides !== undefined) payload.overrides = overrides
    return this.call<AutoBookResolveResult>('autobook.resolve_capture', payload)
  }

  // ─── 测试辅助 ───

  /** 获取当前 pending 请求数（用于测试），仅调试用，生产环境不暴露 */
  _pendingCountForTest(): number {
    return this.pending.size
  }

  /** @deprecated 使用 _pendingCountForTest 替代；保留此名称以兼容现有测试 */
  pendingCount(): number {
    return this._pendingCountForTest()
  }
}
