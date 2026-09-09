import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { NativeBridge } from '../../platform/bridge/native-bridge'
import {
  BRIDGE_VERSION,
  BridgeCommands,
  BridgeError,
  type BridgeResponse,
} from '../../platform/bridge/bridge-types'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const win = window as any

function installMockBridge() {
  const mockNative = {
    call: vi.fn(),
    getBridgeVersion: vi.fn().mockReturnValue(1),
    ping: vi.fn().mockReturnValue('pong'),
  }
  win.AozoraBridge = mockNative
  return mockNative
}

function simulateResponse(requestId: string, data: unknown, success = true, errorCode?: string, errorMessage?: string) {
  const response: BridgeResponse = success
    ? { requestId, success: true, data }
    : { requestId, success: false, error: { code: errorCode ?? 'unknown', message: errorMessage ?? 'error' } }
  win.__aozoraBridgeRespond(JSON.stringify(response))
}

afterEach(() => {
  vi.useRealTimers()
  delete win.AozoraBridge
  delete win.__aozoraBridgeRespond
  delete win.__aozoraPermissionResult__
  delete win.__aozoraVoicePartial__
  delete win.__aozoraVoiceFinal__
  delete win.__aozoraVoiceError__
  delete win.__aozoraImageProgress__
})

// ─── 类型完整性 ───

describe('Bridge types — 命令完整性', () => {
  const expectedCommands = [
    'bridge.getVersion',
    'transaction.insert',
    'transaction.get',
    'transaction.listByBook',
    'transaction.listByDate',
    'transaction.softDelete',
    'book.list',
    'book.insert',
    'category.list',
    // 第八轮 REQ-8/REQ-7：分类自定义 + 照片图标 + 存钱目标
    'category.insert',
    'category.update',
    'icon.pick',
    'savings.list',
    'savings.insert',
    'savings.update',
    'savings.archive',
    'account.list',
    'account.insert',
    'account.update',
    'account.archive',
    'budget.list',
    'budget.insert',
    'budget.update',
    'budget.delete',
    'pendingMutation.list',
    'pendingMutation.markSyncing',
    'pendingMutation.markFailed',
    'pendingMutation.remove',
    // T-M3-02: 同步引擎
    'sync.getStatus',
    'sync.trigger',
    // T-M4-05a: 提醒调度
    'reminder.getStatus',
    'reminder.set',
    'reminder.checkPermission',
    // T-M4-03c / T-M4-02c: AI 通道（ADR-003）
    'ai.report',
    'ai.interpret',
    'ai.validate',
    'ai.execute',
    // T-M4-04a: 权限管理
    'permissions.getStatus',
    'permissions.request',
    'permissions.openSettings',
    // T-M4-04b 占位：语音
    'voice.startRecording',
    'voice.stopRecording',
    // T-M4-04d: 图片链路
    'image.pick',
    'image.delete',
    // T-M4-04e: 图片记账解析
    'image.parse',
    'image.parse_cancel',
    // T-M5-04a: 隐私同意
    'consent.record',
    'consent.status',
    // T-M5-04b: 数据导出
    'export.create',
    'export.list',
    'export.get',
    'export.download',
    // T-M5-04c: 账号删除
    'account.requestDeletionToken',
    'account.delete',
    // T-M5-05c: 应用内反馈
    'feedback.submit',
    // FIX-002/003/004（真机修复轮）
    'haptic.feedback',
    'share.text',
    'auth.getStatus',
    'auth.triggerSync',
    // T10-3（REQ-19）：登录与安全
    'auth.login',
    'auth.logout',
    'auth.me',
    'auth.updateProfile',
    // T9-5a（REQ-11）：本地展示元数据
    'meta.get',
    'meta.set',
    // T11-2（REQ-24）：桌面小组件刷新
    'widget.refresh',
    // T11-4/T11-5（REQ-26）：定时记账规则
    'scheduled.list',
    'scheduled.insert',
    'scheduled.update',
    'scheduled.archive',
    // T11-7（REQ-28）：聊天角色
    'persona.list',
    'persona.insert',
    'persona.update',
    'persona.archive',
  ]

  it('BridgeCommands 包含所有 74 个命令（含 AI 4 个 + 权限 3 个 + 语音 2 个 + 图片 4 个 + 同意 2 个 + 导出 4 个 + 账号删除 2 个 + 反馈 1 个 + FIX 4 个 + T7-2 账户更新 1 个 + 第八轮分类/图标/存钱 7 个 + T9-5a meta 2 个 + T10-3 auth 4 个 + T11-2 widget 1 个 + T11-4 scheduled 4 个 + T11-7 persona 4 个）', () => {
    const actualCommands = Object.values(BridgeCommands)
    expect(actualCommands).toHaveLength(expectedCommands.length)
    for (const cmd of expectedCommands) {
      expect(actualCommands).toContain(cmd)
    }
  })

  it('BRIDGE_VERSION 为 1', () => {
    expect(BRIDGE_VERSION).toBe(1)
  })
})

// ─── NativeBridge 基础行为 ───

describe('NativeBridge — 基础行为', () => {
  let bridge: NativeBridge

  beforeEach(() => {
    bridge = new NativeBridge()
  })

  it('默认不可用（无 window.AozoraBridge）', () => {
    expect(bridge.isAvailable()).toBe(false)
  })

  it('不可用时 call 返回 bridge_unavailable 错误', async () => {
    await expect(bridge.getVersion()).rejects.toThrow(BridgeError)
    await expect(bridge.getVersion()).rejects.toThrow('Native bridge not available')
  })

  it('pendingCount 初始为 0', () => {
    expect(bridge._pendingCountForTest()).toBe(0)
  })
})

// ─── NativeBridge 与 Mock 桥交互 ───

describe('NativeBridge — 与 Mock 桥交互', () => {
  let bridge: NativeBridge
  let mockNative: ReturnType<typeof installMockBridge>

  beforeEach(() => {
    bridge = new NativeBridge()
    mockNative = installMockBridge()
  })

  it('原生同步抛错时立即释放 pending 和定时器（R-M1-26）', async () => {
    vi.useFakeTimers()
    mockNative.call.mockImplementation(() => { throw new Error('native failure') })
    await expect(bridge.listBooks()).rejects.toThrow('native failure')
    expect(bridge._pendingCountForTest()).toBe(0)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('超时拒绝且迟到响应不影响后续请求（R-M1-26）', async () => {
    vi.useFakeTimers()
    const result = expect(bridge.listBooks()).rejects.toMatchObject({ code: 'timeout' })
    const requestId = mockNative.call.mock.calls[0][1]
    await vi.advanceTimersByTimeAsync(10000)
    await result
    expect(bridge._pendingCountForTest()).toBe(0)
    expect(() => simulateResponse(requestId, [])).not.toThrow()
  })

  it('isAvailable 返回 true', () => {
    expect(bridge.isAvailable()).toBe(true)
  })

  it('call 发送正确参数到原生桥', async () => {
    mockNative.call.mockImplementation((_v: number, requestId: string) => {
      setTimeout(() => simulateResponse(requestId, { version: 1 }), 0)
    })

    const result = await bridge.getVersion()
    expect(result).toBe(1)

    expect(mockNative.call).toHaveBeenCalledTimes(1)
    const [version, requestId, command, payload] = mockNative.call.mock.calls[0]
    expect(version).toBe(BRIDGE_VERSION)
    expect(typeof requestId).toBe('string')
    expect(command).toBe('bridge.getVersion')
    expect(payload).toBe('{}')
  })

  it('请求 ID 唯一', async () => {
    const requestIds: string[] = []
    mockNative.call.mockImplementation((_v: number, requestId: string) => {
      requestIds.push(requestId)
      setTimeout(() => simulateResponse(requestId, {}), 0)
    })

    await bridge.listBooks()
    await bridge.listCategories()

    expect(requestIds).toHaveLength(2)
    expect(requestIds[0]).not.toBe(requestIds[1])
  })

  it('错误响应抛出 BridgeError', async () => {
    mockNative.call.mockImplementation((_v: number, requestId: string) => {
      setTimeout(() => simulateResponse(requestId, null, false, 'not_found', 'Transaction not found'), 0)
    })

    try {
      await bridge.getTransaction('nonexistent')
      expect.unreachable('should have thrown')
    } catch (e) {
      expect(e).toBeInstanceOf(BridgeError)
      expect((e as BridgeError).code).toBe('not_found')
      expect((e as BridgeError).message).toBe('Transaction not found')
    }
  })

  it('pendingCount 反映未完成的请求', () => {
    mockNative.call.mockImplementation(() => { /* 不响应 */ })

    bridge.listBooks()
    expect(bridge.pendingCount()).toBe(1)

    bridge.listCategories()
    expect(bridge.pendingCount()).toBe(2)
  })

  it('版本协商：不支持的版本', async () => {
    mockNative.call.mockImplementation((_v: number, requestId: string) => {
      setTimeout(() => simulateResponse(requestId, null, false, 'unsupported_version', 'Expected v1, got v2'), 0)
    })

    try {
      await bridge.getVersion()
      expect.unreachable('should have thrown')
    } catch (e) {
      expect(e).toBeInstanceOf(BridgeError)
      expect((e as BridgeError).code).toBe('unsupported_version')
    }
  })
})

// ─── 命令参数序列化 ───

describe('NativeBridge — 命令参数序列化', () => {
  let bridge: NativeBridge
  let mockNative: ReturnType<typeof installMockBridge>
  let capturedPayload: string = ''

  beforeEach(() => {
    bridge = new NativeBridge()
    mockNative = installMockBridge()
    mockNative.call.mockImplementation((_v: number, requestId: string, _cmd: string, payload: string) => {
      capturedPayload = payload
      setTimeout(() => simulateResponse(requestId, []), 0)
    })
  })

  it('insertTransaction 传递正确 payload', async () => {
    const tx = {
      id: 'tx-1',
      bookId: 'book-1',
      type: 'expense' as const,
      amountCents: 3500,
      categoryId: 'cat-food',
      note: '午餐',
      bizDate: '2026-09-03',
      occurredAt: 1756900800000,
      source: 'manual' as const,
    }
    await bridge.insertTransaction(tx, 'mutation-1')

    const payload = JSON.parse(capturedPayload)
    expect(payload.tx.id).toBe('tx-1')
    expect(payload.tx.amountCents).toBe(3500)
    expect(payload.mutationId).toBe('mutation-1')
  })

  it('listTransactionsByBook 传递 bookId', async () => {
    await bridge.listTransactionsByBook('book-1')
    const payload = JSON.parse(capturedPayload)
    expect(payload.bookId).toBe('book-1')
  })

  it('listTransactionsByDate 传递日期范围', async () => {
    await bridge.listTransactionsByDate('2026-09-01', '2026-09-30')
    const payload = JSON.parse(capturedPayload)
    expect(payload.bizDateStart).toBe('2026-09-01')
    expect(payload.bizDateEnd).toBe('2026-09-30')
  })
})

// ─── 响应解析 ───

describe('NativeBridge — 响应解析', () => {
  let bridge: NativeBridge
  let mockNative: ReturnType<typeof installMockBridge>

  beforeEach(() => {
    bridge = new NativeBridge()
    mockNative = installMockBridge()
    mockNative.call.mockImplementation(() => {})
  })

  it('忽略无法解析的 JSON 响应', () => {
    bridge.listBooks()

    expect(() => win.__aozoraBridgeRespond('not valid json')).not.toThrow()
    expect(bridge.pendingCount()).toBe(1) // 仍然 pending
  })

  it('忽略 JSON null、数组及不完整响应（R-M1-26）', () => {
    expect(() => win.__aozoraBridgeRespond('null')).not.toThrow()
    expect(() => win.__aozoraBridgeRespond('[]')).not.toThrow()
    expect(() => win.__aozoraBridgeRespond('{}')).not.toThrow()
  })

  it('忽略未知 requestId 的响应', () => {
    bridge.listBooks()

    win.__aozoraBridgeRespond(JSON.stringify({ requestId: 'unknown-id', success: true, data: [] }))
    expect(bridge.pendingCount()).toBe(1) // 不匹配，仍然 pending
  })
})

// ─── R-M1-43 回归：Account / Budget 写命令 ───

describe('NativeBridge — R-M1-43 写命令回归', () => {
  let bridge: NativeBridge
  let mockNative: ReturnType<typeof installMockBridge>
  let capturedCommand = ''
  let capturedPayload = ''

  beforeEach(() => {
    bridge = new NativeBridge()
    mockNative = installMockBridge()
    mockNative.call.mockImplementation((_v: number, _requestId: string, cmd: string, payload: string) => {
      capturedCommand = cmd
      capturedPayload = payload
      setTimeout(() => simulateResponse(_requestId, { id: 'test-id' }), 0)
    })
  })

  it('insertAccount → account.insert', async () => {
    await bridge.insertAccount(
      { id: 'acc-1', ownerId: 'u1', name: '现金', version: 1 },
      'mut-1'
    )
    expect(capturedCommand).toBe('account.insert')
    const payload = JSON.parse(capturedPayload)
    expect(payload.account.id).toBe('acc-1')
    expect(payload.account.name).toBe('现金')
    expect(payload.mutationId).toBe('mut-1')
  })

  it('archiveAccount → account.archive', async () => {
    await bridge.archiveAccount('acc-1', 'mut-2')
    expect(capturedCommand).toBe('account.archive')
    const payload = JSON.parse(capturedPayload)
    expect(payload.id).toBe('acc-1')
    expect(payload.mutationId).toBe('mut-2')
  })

  it('insertBudget → budget.insert', async () => {
    await bridge.insertBudget(
      { id: 'bgt-1', bookId: 'book-1', budgetType: 'monthly', categoryId: null, period: '2026-09', amountCents: 300000, version: 1 },
      'mut-3'
    )
    expect(capturedCommand).toBe('budget.insert')
    const payload = JSON.parse(capturedPayload)
    expect(payload.budget.id).toBe('bgt-1')
    expect(payload.budget.amountCents).toBe(300000)
    expect(payload.mutationId).toBe('mut-3')
  })

  it('updateBudget → budget.update', async () => {
    await bridge.updateBudget({ id: 'bgt-1', amountCents: 500000, version: 2 }, 'mut-4')
    expect(capturedCommand).toBe('budget.update')
    const payload = JSON.parse(capturedPayload)
    expect(payload.budget.amountCents).toBe(500000)
    expect(payload.budget.version).toBe(2)
    expect(payload.mutationId).toBe('mut-4')
  })

  it('deleteBudget → budget.delete', async () => {
    await bridge.deleteBudget('bgt-1', 'mut-5')
    expect(capturedCommand).toBe('budget.delete')
    const payload = JSON.parse(capturedPayload)
    expect(payload.id).toBe('bgt-1')
    expect(payload.mutationId).toBe('mut-5')
  })
})

// ─── T-M4-03c: AI 洞察桥命令 ───

describe('NativeBridge — T-M4-03c AI 洞察', () => {
  let bridge: NativeBridge
  let mockNative: ReturnType<typeof installMockBridge>
  let capturedCommand = ''
  let capturedPayload = ''

  beforeEach(() => {
    bridge = new NativeBridge()
    mockNative = installMockBridge()
    mockNative.call.mockImplementation((_v: number, requestId: string, cmd: string, payload: string) => {
      capturedCommand = cmd
      capturedPayload = payload
      setTimeout(() => simulateResponse(requestId, {
        request_id: 'req-1',
        book_id: 'book-1',
        period: '2026-06',
        data_hash: 'abc123',
        total_income: 100000,
        total_expense: 80000,
        balance: 20000,
        transaction_count: 10,
        expense_by_category: [],
        income_by_category: [],
        budget: null,
        largest_expense: null,
        largest_income: null,
        trend: [],
        narrative: '本月收支良好',
        narrative_cached: false,
        usage: null,
      }), 0)
    })
  })

  it('reportAI → ai.report 命令，payload 含 bookId 和 period', async () => {
    const result = await bridge.reportAI('book-1', '2026-06')
    expect(capturedCommand).toBe('ai.report')
    const payload = JSON.parse(capturedPayload)
    expect(payload.bookId).toBe('book-1')
    expect(payload.period).toBe('2026-06')
    expect(result.data_hash).toBe('abc123')
    expect(result.narrative).toBe('本月收支良好')
  })
})

// ─── T-M4-04a: 权限管理桥命令 ───

describe('NativeBridge — T-M4-04a 权限管理', () => {
  let bridge: NativeBridge
  let mockNative: ReturnType<typeof installMockBridge>
  let capturedCommand = ''
  let capturedPayload = ''

  beforeEach(() => {
    bridge = new NativeBridge()
    mockNative = installMockBridge()
    mockNative.call.mockImplementation((_v: number, requestId: string, cmd: string, payload: string) => {
      capturedCommand = cmd
      capturedPayload = payload
      setTimeout(() => simulateResponse(requestId, {
        permissions: { RECORD_AUDIO: 'granted', CAMERA: 'not_requested', READ_MEDIA_IMAGES: 'denied' },
        canRecordAudio: true,
        canUseCamera: false,
        canAccessImages: false,
      }), 0)
    })
  })

  it('getPermissionStatus → permissions.getStatus', async () => {
    const result = await bridge.getPermissionStatus()
    expect(capturedCommand).toBe('permissions.getStatus')
    expect(result.canRecordAudio).toBe(true)
    expect(result.canUseCamera).toBe(false)
    expect(result.permissions.RECORD_AUDIO).toBe('granted')
    expect(result.permissions.CAMERA).toBe('not_requested')
  })

  it('requestPermission → permissions.request，payload 含 permission', async () => {
    mockNative.call.mockImplementation((_v: number, requestId: string, cmd: string, payload: string) => {
      capturedCommand = cmd
      capturedPayload = payload
      setTimeout(() => simulateResponse(requestId, { pending: true }), 0)
    })
    const result = await bridge.requestPermission('CAMERA')
    expect(capturedCommand).toBe('permissions.request')
    const payload = JSON.parse(capturedPayload)
    expect(payload.permission).toBe('CAMERA')
    expect(result.pending).toBe(true)
  })

  it('openPermissionSettings → permissions.openSettings', async () => {
    mockNative.call.mockImplementation((_v: number, requestId: string, cmd: string) => {
      capturedCommand = cmd
      setTimeout(() => simulateResponse(requestId, { success: true }), 0)
    })
    await bridge.openPermissionSettings()
    expect(capturedCommand).toBe('permissions.openSettings')
  })

  it('onPermissionResult 监听并取消订阅', () => {
    const results: unknown[] = []
    const unsubscribe = bridge.onPermissionResult(e => results.push(e))

    // 模拟原生推送
    win.__aozoraPermissionResult__(JSON.stringify({ permission: 'CAMERA', status: 'granted' }))
    expect(results).toHaveLength(1)
    expect((results[0] as { permission: string }).permission).toBe('CAMERA')

    // 取消订阅后，handler 被移除
    unsubscribe()
    // 取消订阅后不再接收推送（handler 已恢复为之前的值）
    expect(typeof win.__aozoraPermissionResult__).not.toBe('function')
  })
})

// ─── T-M4-02c: AI 助手对话桥命令 ───

describe('NativeBridge — T-M4-02c AI 助手对话', () => {
  let bridge: NativeBridge
  let capturedCommand = ''
  let capturedPayload = ''

  beforeEach(() => {
    bridge = new NativeBridge()
    installMockBridge()
    const mockNative = (window as any).AozoraBridge
    mockNative.call.mockImplementation((_v: number, requestId: string, cmd: string, payload: string) => {
      capturedCommand = cmd
      capturedPayload = payload
      setTimeout(() => {
        if (cmd === 'ai.interpret') {
          simulateResponse(requestId, {
            request_id: 'req-1', task: 'ledger_parse', model: 'qwen3.7-flash',
            output: { type: 'expense', amountCents: 1250, category: '餐饮', note: '午饭', bizDate: '', confidence: 0.95 },
            usage: { prompt_tokens: 100, completion_tokens: 50 },
          })
        } else if (cmd === 'ai.validate') {
          simulateResponse(requestId, { valid: true, errors: [], verdict: 'accepted' })
        } else if (cmd === 'ai.execute') {
          simulateResponse(requestId, { transaction_ids: ['tx-1', 'tx-2'], proposal_id: 'prop-1', undo_window_seconds: 5 })
        }
      }, 0)
    })
  })

  it('interpretAI → ai.interpret 命令，payload 含 input', async () => {
    const result = await bridge.interpretAI('午饭 12.5')
    expect(capturedCommand).toBe('ai.interpret')
    const payload = JSON.parse(capturedPayload)
    expect(payload.input).toBe('午饭 12.5')
    expect(result.output).toBeDefined()
  })

  // T11-6（REQ-27 / 决策 #7）：context 注入管线
  it('interpretAI 带 context → payload 含 context={profile, persona}（原样对象）', async () => {
    const result = await bridge.interpretAI('午饭 12.5', {
      profile: { nickname: '老板', communicationStyle: '简洁直接' },
      persona: null,
    })
    expect(capturedCommand).toBe('ai.interpret')
    const payload = JSON.parse(capturedPayload)
    expect(payload.input).toBe('午饭 12.5')
    expect(payload.context).toEqual({
      profile: { nickname: '老板', communicationStyle: '简洁直接' },
      persona: null,
    })
    expect(result.output).toBeDefined()
  })

  it('interpretAI context=null/省略 → payload 不带 context 键（服务端旧行为）', async () => {
    await bridge.interpretAI('午饭 12.5', null)
    expect(JSON.parse(capturedPayload)).toEqual({ input: '午饭 12.5' })
    await bridge.interpretAI('午饭 12.5')
    expect(JSON.parse(capturedPayload)).toEqual({ input: '午饭 12.5' })
  })

  it('validateProposal → ai.validate 命令，payload 含 proposal 和 book_id', async () => {
    const result = await bridge.validateProposal({ proposalId: 'p1', action: 'add_tx' }, 'book-1')
    expect(capturedCommand).toBe('ai.validate')
    const payload = JSON.parse(capturedPayload)
    expect(payload.proposal).toBeDefined()
    expect(payload.book_id).toBe('book-1')
    expect(result.valid).toBe(true)
    expect(result.verdict).toBe('accepted')
  })

  it('executeProposal → ai.execute 命令，payload 含 proposal/book_id/confirmation_token', async () => {
    const result = await bridge.executeProposal({ proposalId: 'p1' }, 'book-1', 'token-123')
    expect(capturedCommand).toBe('ai.execute')
    const payload = JSON.parse(capturedPayload)
    expect(payload.proposal).toBeDefined()
    expect(payload.book_id).toBe('book-1')
    expect(payload.confirmation_token).toBe('token-123')
    expect(result.transaction_ids).toEqual(['tx-1', 'tx-2'])
    expect(result.undo_window_seconds).toBe(5)
  })
})

// ─── T-M4-04b: 语音识别桥命令 ───

describe('NativeBridge — T-M4-04b 语音识别', () => {
  let bridge: NativeBridge
  let mockNative: ReturnType<typeof installMockBridge>
  let capturedCommand = ''

  beforeEach(() => {
    bridge = new NativeBridge()
    mockNative = installMockBridge()
    mockNative.call.mockImplementation((_v: number, requestId: string, cmd: string) => {
      capturedCommand = cmd
      setTimeout(() => simulateResponse(requestId, { started: true }), 0)
    })
  })

  it('startVoiceRecording → voice.startRecording 命令', async () => {
    const result = await bridge.startVoiceRecording()
    expect(capturedCommand).toBe('voice.startRecording')
    expect(result.started).toBe(true)
  })

  it('stopVoiceRecording → voice.stopRecording 命令，返回转写文本', async () => {
    mockNative.call.mockImplementation((_v: number, requestId: string, cmd: string) => {
      capturedCommand = cmd
      setTimeout(() => simulateResponse(requestId, { stopped: true, text: '你好世界' }), 0)
    })
    const result = await bridge.stopVoiceRecording()
    expect(capturedCommand).toBe('voice.stopRecording')
    expect(result.stopped).toBe(true)
    expect(result.text).toBe('你好世界')
  })

  it('startVoiceRecording 失败时抛出 BridgeError', async () => {
    mockNative.call.mockImplementation((_v: number, requestId: string) => {
      setTimeout(() => simulateResponse(requestId, null, false, 'permission_denied', 'RECORD_AUDIO not granted'), 0)
    })
    await expect(bridge.startVoiceRecording()).rejects.toMatchObject({
      code: 'permission_denied',
    })
  })

  it('onVoicePartial 监听实时部分转写', () => {
    const texts: string[] = []
    const unsubscribe = bridge.onVoicePartial(t => texts.push(t))
    win.__aozoraVoicePartial__('你')
    win.__aozoraVoicePartial__('你好')
    expect(texts).toEqual(['你', '你好'])
    unsubscribe()
  })

  it('onVoiceFinal 监听最终转写结果', () => {
    let finalText = ''
    const unsubscribe = bridge.onVoiceFinal(t => { finalText = t })
    win.__aozoraVoiceFinal__('你好世界')
    expect(finalText).toBe('你好世界')
    unsubscribe()
  })

  it('onVoiceError 监听语音错误（对象格式）', () => {
    const errors: Array<{ code: string; message: string }> = []
    const unsubscribe = bridge.onVoiceError(e => errors.push(e))
    win.__aozoraVoiceError__({ code: 'model_not_loaded', message: 'Vosk model not loaded' })
    expect(errors).toHaveLength(1)
    expect(errors[0].code).toBe('model_not_loaded')
    unsubscribe()
  })

  it('onVoiceError 兼容旧版字符串格式（R-M4-04b Bug-B4）', () => {
    const errors: Array<{ code: string; message: string }> = []
    const unsubscribe = bridge.onVoiceError(e => errors.push(e))
    win.__aozoraVoiceError__(JSON.stringify({ code: 'start_error', message: 'audio init failed' }))
    expect(errors).toHaveLength(1)
    expect(errors[0].code).toBe('start_error')
    unsubscribe()
  })
})

// ─── T-M4-04d: 图片链路桥命令 ───

describe('NativeBridge — T-M4-04d 图片链路', () => {
  let bridge: NativeBridge
  let mockNative: ReturnType<typeof installMockBridge>
  let capturedCommand = ''
  let capturedPayload = ''

  beforeEach(() => {
    bridge = new NativeBridge()
    mockNative = installMockBridge()
  })

  it('pickImage → image.pick 命令，payload 含 source 和 quality', async () => {
    mockNative.call.mockImplementation((_v: number, requestId: string, cmd: string, payload: string) => {
      capturedCommand = cmd
      capturedPayload = payload
      setTimeout(() => simulateResponse(requestId, {
        attachmentId: 'att-1',
        mimeType: 'image/jpeg',
        fileSize: 102400,
      }), 0)
    })
    const result = await bridge.pickImage('gallery', 85)
    expect(capturedCommand).toBe('image.pick')
    const payload = JSON.parse(capturedPayload)
    expect(payload.source).toBe('gallery')
    expect(payload.quality).toBe(85)
    expect(result.attachmentId).toBe('att-1')
    expect(result.mimeType).toBe('image/jpeg')
    expect(result.fileSize).toBe(102400)
  })

  it('pickImage 使用默认 quality=80', async () => {
    mockNative.call.mockImplementation((_v: number, requestId: string, _cmd: string, payload: string) => {
      capturedPayload = payload
      setTimeout(() => simulateResponse(requestId, { attachmentId: 'att-2', mimeType: 'image/jpeg', fileSize: 50000 }), 0)
    })
    await bridge.pickImage('camera')
    const payload = JSON.parse(capturedPayload)
    expect(payload.source).toBe('camera')
    expect(payload.quality).toBe(80)
  })

  it('pickImage 不设固定超时（timeoutMs=0），用户可能在相机停留很久', () => {
    vi.useFakeTimers()
    mockNative.call.mockImplementation(() => { /* 不响应 */ })
    bridge.pickImage('camera')
    // 推进 60 秒，不应超时（默认命令 10s 超时）
    vi.advanceTimersByTime(60_000)
    expect(bridge.pendingCount()).toBe(1)
    vi.useRealTimers()
  })

  it('deleteImage → image.delete 命令，payload 含 attachmentId', async () => {
    mockNative.call.mockImplementation((_v: number, requestId: string, cmd: string, payload: string) => {
      capturedCommand = cmd
      capturedPayload = payload
      setTimeout(() => simulateResponse(requestId, { success: true }), 0)
    })
    const result = await bridge.deleteImage('att-1')
    expect(capturedCommand).toBe('image.delete')
    const payload = JSON.parse(capturedPayload)
    expect(payload.attachmentId).toBe('att-1')
    expect(result.success).toBe(true)
  })

  it('onImageProgress 监听上传进度', () => {
    const progress: Array<{ sent: number; total: number }> = []
    const unsubscribe = bridge.onImageProgress(e => progress.push(e))
    win.__aozoraImageProgress__({ sent: 51200, total: 102400 })
    win.__aozoraImageProgress__({ sent: 102400, total: 102400 })
    expect(progress).toEqual([
      { sent: 51200, total: 102400 },
      { sent: 102400, total: 102400 },
    ])
    unsubscribe()
  })

  it('pickImage cancelled 时抛出 BridgeError code=cancelled', async () => {
    mockNative.call.mockImplementation((_v: number, requestId: string) => {
      setTimeout(() => simulateResponse(requestId, null, false, 'cancelled', 'Image picker cancelled'), 0)
    })
    await expect(bridge.pickImage('camera')).rejects.toMatchObject({ code: 'cancelled' })
  })

  it('parseImage → image.parse 命令，payload 含 attachmentId 和 bookId，超时 120s（T-M4-04e）', async () => {
    mockNative.call.mockImplementation((_v: number, requestId: string, cmd: string, payload: string) => {
      capturedCommand = cmd
      capturedPayload = payload
      setTimeout(() => simulateResponse(requestId, {
        request_id: 'req-1',
        proposal: { proposalId: 'prop-1', transactions: [], confidence: 0.9 },
        attachment_id: 'att-1',
        confidence: 0.9,
        usage: { prompt_tokens: 100, completion_tokens: 50 },
      }), 0)
    })
    const result = await bridge.parseImage('att-1', 'book-1')
    expect(capturedCommand).toBe('image.parse')
    const payload = JSON.parse(capturedPayload)
    expect(payload.attachmentId).toBe('att-1')
    expect(payload.bookId).toBe('book-1')
    expect(result.attachment_id).toBe('att-1')
    expect(result.proposal.proposalId).toBe('prop-1')
  })

  it('cancelImageParse → image.parse_cancel 命令，fire-and-forget（NEW-R6-2 fix）', () => {
    mockNative.call.mockImplementation(() => {})
    bridge.cancelImageParse()
    const cancelCall = mockNative.call.mock.calls.find(c => c[2] === 'image.parse_cancel')
    expect(cancelCall).toBeDefined()
    expect(cancelCall![3]).toBe('{}')
  })
})

// ─── T-M5-04a: 隐私同意桥命令 ───

describe('NativeBridge — T-M5-04a 隐私同意', () => {
  let bridge: NativeBridge
  let capturedCommand = ''
  let capturedPayload = ''

  beforeEach(() => {
    bridge = new NativeBridge()
    installMockBridge().call.mockImplementation((_v: number, requestId: string, cmd: string, payload: string) => {
      capturedCommand = cmd
      capturedPayload = payload
      setTimeout(() => simulateResponse(requestId, {
        id: 'consent-1',
        policyVersion: 'v1',
        consentedAt: Date.now(),
      }), 0)
    })
  })

  it('recordConsent 发送 consent.record 命令', async () => {
    const result = await bridge.recordConsent({ policyVersion: 'v1' })
    expect(capturedCommand).toBe('consent.record')
    const payload = JSON.parse(capturedPayload)
    expect(payload.policyVersion).toBe('v1')
    expect(result.id).toBe('consent-1')
  })

  it('recordConsent 默认 policyVersion 为 v1', async () => {
    await bridge.recordConsent()
    const payload = JSON.parse(capturedPayload)
    expect(payload.policyVersion).toBe('v1')
  })

  it('getConsentStatus 发送 consent.status 命令', async () => {
    installMockBridge().call.mockImplementation((_v: number, requestId: string) => {
      setTimeout(() => simulateResponse(requestId, { hasConsented: true }), 0)
    })
    const freshBridge = new NativeBridge()
    const result = await freshBridge.getConsentStatus()
    expect(result.hasConsented).toBe(true)
  })
})

// ─── T-M5-04b: 数据导出桥命令 ───

describe('NativeBridge — T-M5-04b 数据导出', () => {
  let bridge: NativeBridge
  let capturedCommand = ''
  let capturedPayload = ''

  beforeEach(() => {
    bridge = new NativeBridge()
    installMockBridge().call.mockImplementation((_v: number, requestId: string, cmd: string, payload: string) => {
      capturedCommand = cmd
      capturedPayload = payload
      setTimeout(() => simulateResponse(requestId, {
        id: 'exp-1',
        bookId: 'book-1',
        format: 'json',
        status: 'completed',
        createdAt: '2026-09-07T10:00:00Z',
        fileSize: 1234,
      }), 0)
    })
  })

  it('createExport 发送 export.create 命令', async () => {
    const result = await bridge.createExport({ bookId: 'book-1', format: 'json' })
    expect(capturedCommand).toBe('export.create')
    const payload = JSON.parse(capturedPayload)
    expect(payload.bookId).toBe('book-1')
    expect(payload.format).toBe('json')
    expect(result.id).toBe('exp-1')
    expect(result.status).toBe('completed')
  })

  it('createExport 默认 format 为 json', async () => {
    await bridge.createExport({ bookId: 'book-1' })
    const payload = JSON.parse(capturedPayload)
    expect(payload.format).toBe('json')
  })

  it('listExports 发送 export.list 命令', async () => {
    installMockBridge().call.mockImplementation((_v: number, requestId: string, cmd: string) => {
      capturedCommand = cmd
      setTimeout(() => simulateResponse(requestId, [{
        id: 'exp-1', bookId: 'book-1', format: 'json', status: 'completed',
        createdAt: '2026-09-07T10:00:00Z',
      }]), 0)
    })
    const freshBridge = new NativeBridge()
    const result = await freshBridge.listExports()
    expect(capturedCommand).toBe('export.list')
    expect(result).toHaveLength(1)
    expect(result[0].id).toBe('exp-1')
  })

  it('getExport 发送 export.get 命令', async () => {
    const result = await bridge.getExport('exp-1')
    expect(capturedCommand).toBe('export.get')
    const payload = JSON.parse(capturedPayload)
    expect(payload.exportId).toBe('exp-1')
    expect(result.id).toBe('exp-1')
  })

  it('downloadExport 发送 export.download 命令并返回内容', async () => {
    installMockBridge().call.mockImplementation((_v: number, requestId: string, cmd: string) => {
      capturedCommand = cmd
      setTimeout(() => simulateResponse(requestId, {
        localPath: '/Download/Aozora/export.json',
        filename: 'export.json',
        content: '[{"amount_cents":100,"type":"expense"}]',
      }), 0)
    })
    const freshBridge = new NativeBridge()
    const result = await freshBridge.downloadExport('exp-1')
    expect(capturedCommand).toBe('export.download')
    expect(result.filename).toBe('export.json')
    expect(result.content).toContain('amount_cents')
  })

  it('downloadExport 桥错误（expired）抛出 BridgeError', async () => {
    installMockBridge().call.mockImplementation((_v: number, requestId: string) => {
      setTimeout(() => simulateResponse(requestId, null, false, 'expired', 'Export has expired'), 0)
    })
    const freshBridge = new NativeBridge()
    await expect(freshBridge.downloadExport('exp-1')).rejects.toThrow(BridgeError)
  })
})

// ─── T-M5-04c: 账号删除桥命令 ───

describe('NativeBridge — T-M5-04c 账号删除', () => {
  let bridge: NativeBridge
  let capturedCommand = ''
  let capturedPayload = ''

  beforeEach(() => {
    bridge = new NativeBridge()
    installMockBridge().call.mockImplementation((_v: number, requestId: string, cmd: string, payload: string) => {
      capturedCommand = cmd
      capturedPayload = payload
      setTimeout(() => simulateResponse(requestId, {
        confirmToken: 'tok-abc',
        expiresAt: '2026-09-07T10:05:00Z',
      }), 0)
    })
  })

  it('requestAccountDeletionToken 发送 account.requestDeletionToken 命令', async () => {
    const result = await bridge.requestAccountDeletionToken()
    expect(capturedCommand).toBe('account.requestDeletionToken')
    expect(capturedPayload).toBe('{}')
    expect(result.confirmToken).toBe('tok-abc')
    expect(result.expiresAt).toContain('2026-09-07')
  })

  it('deleteAccount 发送 account.delete 命令并序列化 confirmToken', async () => {
    installMockBridge().call.mockImplementation((_v: number, requestId: string, cmd: string, payload: string) => {
      capturedCommand = cmd
      capturedPayload = payload
      setTimeout(() => simulateResponse(requestId, {
        status: 'frozen',
        frozenAt: '2026-09-07T10:00:00Z',
        message: 'Account frozen; data will be deleted in 7 days.',
      }), 0)
    })
    const freshBridge = new NativeBridge()
    const result = await freshBridge.deleteAccount('confirm-xyz')
    expect(capturedCommand).toBe('account.delete')
    const payload = JSON.parse(capturedPayload)
    expect(payload.confirmToken).toBe('confirm-xyz')
    expect(result.status).toBe('frozen')
    expect(result.frozenAt).toContain('2026-09-07')
  })

  it('deleteAccount 桥错误（invalid_confirm_token）抛出 BridgeError', async () => {
    installMockBridge().call.mockImplementation((_v: number, requestId: string) => {
      setTimeout(() => simulateResponse(requestId, null, false, 'invalid_confirm_token', 'Invalid confirmation token'), 0)
    })
    const freshBridge = new NativeBridge()
    await expect(freshBridge.deleteAccount('bogus')).rejects.toThrow(BridgeError)
    try {
      await freshBridge.deleteAccount('bogus')
    } catch (err) {
      expect(err).toBeInstanceOf(BridgeError)
      expect((err as BridgeError).code).toBe('invalid_confirm_token')
    }
  })

  it('deleteAccount 桥错误（confirm_token_expired）抛出 BridgeError', async () => {
    installMockBridge().call.mockImplementation((_v: number, requestId: string) => {
      setTimeout(() => simulateResponse(requestId, null, false, 'confirm_token_expired', 'Token has expired'), 0)
    })
    const freshBridge = new NativeBridge()
    try {
      await freshBridge.deleteAccount('stale-token')
      expect.unreachable('should have thrown')
    } catch (err) {
      expect(err).toBeInstanceOf(BridgeError)
      expect((err as BridgeError).code).toBe('confirm_token_expired')
    }
  })
})

// ─── T-M5-05c: 应用内反馈桥命令 ───

describe('NativeBridge — T-M5-05c 应用内反馈', () => {
  let bridge: NativeBridge
  let capturedCommand = ''
  let capturedPayload = ''

  beforeEach(() => {
    bridge = new NativeBridge()
    installMockBridge().call.mockImplementation((_v: number, requestId: string, cmd: string, payload: string) => {
      capturedCommand = cmd
      capturedPayload = payload
      setTimeout(() => simulateResponse(requestId, { id: 'fb-uuid-1' }), 0)
    })
  })

  it('submitFeedback 发送 feedback.submit 命令，payload 含 category/content/contact', async () => {
    const result = await bridge.submitFeedback({ category: 'bug', content: '闪退在记账页', contact: 'user@example.com' })
    expect(capturedCommand).toBe('feedback.submit')
    const payload = JSON.parse(capturedPayload)
    expect(payload.category).toBe('bug')
    expect(payload.content).toBe('闪退在记账页')
    expect(payload.contact).toBe('user@example.com')
    expect(result.id).toBe('fb-uuid-1')
  })

  it('submitFeedback 不设 contact 时 payload 中 contact 为 undefined', async () => {
    await bridge.submitFeedback({ category: 'suggestion', content: '希望支持语音备注' })
    const payload = JSON.parse(capturedPayload)
    expect(payload.category).toBe('suggestion')
    expect(payload.content).toBe('希望支持语音备注')
    expect(payload.contact).toBeUndefined()
  })

  it('submitFeedback 桥错误（rate_limited）抛出 BridgeError', async () => {
    installMockBridge().call.mockImplementation((_v: number, requestId: string) => {
      setTimeout(() => simulateResponse(requestId, null, false, 'rate_limited', 'Too many requests'), 0)
    })
    const freshBridge = new NativeBridge()
    try {
      await freshBridge.submitFeedback({ category: 'bug', content: 'test' })
      expect.unreachable('should have thrown')
    } catch (err) {
      expect(err).toBeInstanceOf(BridgeError)
      expect((err as BridgeError).code).toBe('rate_limited')
    }
  })
})
