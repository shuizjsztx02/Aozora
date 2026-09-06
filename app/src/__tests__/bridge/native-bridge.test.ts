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
    'account.list',
    'account.insert',
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
  ]

  it('BridgeCommands 包含所有 22 个命令（含 T-M3-02 同步 2 个命令）', () => {
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
    expect(bridge.pendingCount()).toBe(0)
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
    expect(bridge.pendingCount()).toBe(0)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('超时拒绝且迟到响应不影响后续请求（R-M1-26）', async () => {
    vi.useFakeTimers()
    const result = expect(bridge.listBooks()).rejects.toMatchObject({ code: 'timeout' })
    const requestId = mockNative.call.mock.calls[0][1]
    await vi.advanceTimersByTimeAsync(10000)
    await result
    expect(bridge.pendingCount()).toBe(0)
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
