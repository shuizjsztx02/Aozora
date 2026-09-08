/**
 * 第八轮 Bug-6/T8-6: use-assistant 免确认本地记账测试。
 *
 * 验证：
 * - 解析成功 → 直接 bridge.insertTransaction 本地写入（不调 executeProposal）
 * - 编辑 → softDelete 旧 + insert 新（替换写）
 * - 撤销 → softDelete 全部 + 卡片关闭
 * - bookId 缺失 / 写入失败 → 错误可见
 * - 非今日 bizDate → occurredAt 取该日本地正午（决策 #5 对齐）
 * - chat 提案 → 不写库不弹卡
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useAssistant } from '../../features/assistant/use-assistant'
import { parseInput } from '../../domain/nlu/parser'
import type { ActionProposal } from '../../domain/action-proposal'
import { todayBizDate } from '../../domain/date'

const mockBridge = vi.hoisted(() => ({
  insertTransaction: vi.fn().mockResolvedValue({ id: 'tx-1', version: 1 }),
  softDeleteTransaction: vi.fn().mockResolvedValue({ success: true }),
  triggerSync: vi.fn().mockResolvedValue(undefined),
  executeProposal: vi.fn(),
  validateProposal: vi.fn().mockResolvedValue({ valid: true }),
  isAvailable: vi.fn().mockReturnValue(false),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

vi.mock('../../domain/nlu/parser', () => ({
  parseInput: vi.fn(),
}))

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

const TZ = 'Asia/Shanghai'

function txProposal(overrides: Partial<ActionProposal> = {}): ActionProposal {
  return {
    proposalId: 'prop-1',
    action: 'add_tx',
    confidence: 0.9,
    requiresConfirmation: true,
    message: '记下这笔',
    warnings: [],
    transactions: [{
      type: 'expense',
      amountCents: 67800,
      categoryId: 'cat-food',
      bizDate: todayBizDate(TZ),
      note: '吃饭',
    }],
    ...overrides,
  }
}

function setup(bookId: string | null = 'book-1') {
  return renderHook(() => useAssistant({ categories: [], bookId, timezone: TZ }))
}

describe('use-assistant — 免确认本地记账（T8-6）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.insertTransaction.mockResolvedValue({ id: 'tx-1', version: 1 })
    mockBridge.softDeleteTransaction.mockResolvedValue({ success: true })
    mockBridge.isAvailable.mockReturnValue(false)
  })

  it('解析成功直接本地写入，不调用 executeProposal，卡片进入 committed + 撤销窗', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    const { result } = setup()

    await act(async () => { await result.current.submitInput('吃饭678') })

    await waitFor(() => expect(result.current.cardStatus).toBe('committed'))
    expect(mockBridge.executeProposal).not.toHaveBeenCalled()
    expect(mockBridge.insertTransaction).toHaveBeenCalledTimes(1)

    const [tx, mutationId] = mockBridge.insertTransaction.mock.calls[0]
    expect(tx.bookId).toBe('book-1')
    expect(tx.amountCents).toBe(67800)
    expect(tx.categoryId).toBe('cat-food')
    expect(tx.source).toBe('nlu_ai')
    expect(tx.id).toMatch(UUID_RE)
    expect(mutationId).toMatch(UUID_RE)

    // 今日 → occurredAt ≈ now
    expect(Date.now() - tx.occurredAt).toBeLessThan(5_000)

    const last = result.current.messages[result.current.messages.length - 1]
    expect(last.proposalStatus).toBe('committed')
    expect(last.transactionIds).toHaveLength(1)
    expect(last.text).toBe('已记录 1 笔交易')
    // 卡片常驻 + 撤销窗开启
    expect(result.current.activeProposal).not.toBeNull()
    expect(result.current.phase).toBe('undo_window')
    expect(mockBridge.triggerSync).toHaveBeenCalled()
  })

  it('非今日 bizDate → occurredAt 取该日本地正午（决策 #5 对齐）', async () => {
    const p = txProposal()
    p.transactions![0].bizDate = '2026-09-01'
    vi.mocked(parseInput).mockReturnValue(p)
    const { result } = setup()

    await act(async () => { await result.current.submitInput('吃饭678') })

    const [tx] = mockBridge.insertTransaction.mock.calls[0]
    expect(tx.bizDate).toBe('2026-09-01')
    expect(tx.occurredAt).toBe(new Date('2026-09-01T12:00:00').getTime())
  })

  it('bookId 缺失 → 明确报错且不写入', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    const { result } = setup(null)

    await act(async () => { await result.current.submitInput('吃饭678') })

    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
    expect(result.current.activeProposal).toBeNull()
    expect(result.current.phase).toBe('idle')
    const last = result.current.messages[result.current.messages.length - 1]
    expect(last.text).toContain('账本未就绪')
  })

  it('编辑 = softDelete 旧笔 + insert 新值（替换写，无 update 命令）', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    const { result } = setup()

    await act(async () => { await result.current.submitInput('吃饭678') })
    await waitFor(() => expect(result.current.cardStatus).toBe('committed'))
    const writtenId = mockBridge.insertTransaction.mock.calls[0][0].id as string

    const edited = txProposal()
    edited.transactions![0].amountCents = 5000
    edited.transactions![0].note = '午饭'
    await act(async () => { await result.current.editProposal(edited) })

    expect(mockBridge.softDeleteTransaction).toHaveBeenCalledTimes(1)
    expect(mockBridge.softDeleteTransaction.mock.calls[0][0]).toBe(writtenId)
    expect(mockBridge.softDeleteTransaction.mock.calls[0][1]).toMatch(UUID_RE)
    expect(mockBridge.insertTransaction).toHaveBeenCalledTimes(2)
    const [tx2] = mockBridge.insertTransaction.mock.calls[1]
    expect(tx2.amountCents).toBe(5000)
    expect(tx2.note).toBe('午饭')

    await waitFor(() => {
      const m = result.current.messages.find(x => x.proposal?.proposalId === 'prop-1')
      expect(m?.text).toBe('已更新记录')
      expect(m?.proposalStatus).toBe('committed')
    })
    // 编辑后撤销窗不重启（编辑立即生效）
    expect(result.current.phase).toBe('idle')
  })

  it('撤销 → softDelete 已写入交易并关闭卡片', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    const { result } = setup()

    await act(async () => { await result.current.submitInput('吃饭678') })
    await waitFor(() => expect(result.current.cardStatus).toBe('committed'))
    const writtenId = mockBridge.insertTransaction.mock.calls[0][0].id as string

    await act(async () => { await result.current.undoExecution() })

    expect(mockBridge.softDeleteTransaction).toHaveBeenCalledWith(writtenId, expect.any(String))
    expect(result.current.activeProposal).toBeNull()
    expect(result.current.cardStatus).toBeNull()
    const m = result.current.messages.find(x => x.proposal?.proposalId === 'prop-1')
    expect(m?.proposalStatus).toBe('undone')
    expect(m?.text).toBe('已撤销')
  })

  it('本地写入失败 → error 可见，卡片保留可编辑重试', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    mockBridge.insertTransaction.mockRejectedValueOnce(new Error('db down'))
    const { result } = setup()

    await act(async () => { await result.current.submitInput('吃饭678') })

    expect(result.current.cardStatus).toBe('error')
    expect(result.current.activeProposal).not.toBeNull()
    expect(result.current.phase).toBe('idle')
    const last = result.current.messages[result.current.messages.length - 1]
    expect(last.text).toContain('❌ 记账失败：db down')
  })

  it('chat 提案 → 正常落消息，不写库不弹卡', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal({
      action: 'chat',
      transactions: undefined,
      message: '你好呀',
    }))
    const { result } = setup()

    await act(async () => { await result.current.submitInput('你好') })

    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
    expect(result.current.activeProposal).toBeNull()
    const last = result.current.messages[result.current.messages.length - 1]
    expect(last.text).toBe('你好呀')
    expect(result.current.phase).toBe('idle')
  })
})
