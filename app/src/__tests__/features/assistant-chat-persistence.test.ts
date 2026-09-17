/**
 * T14-4（REQ-42 / 决策 #7）：聊天历史切页不丢——use-assistant 与 store 接线测试。
 *
 * 验证：unmount（模拟切页）→ remount 后 messages 从 store 恢复；
 * processing/undo 窗/编辑卡等瞬时态不复活（无过期可点撤销钮）；
 * clear（登出挂钩行为）后 remount 为空。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useAssistant } from '../../features/assistant/use-assistant'
import { useAssistantChatStore } from '../../platform/store/assistant-chat-store'
import { parseInput } from '../../domain/nlu/parser'
import type { ActionProposal } from '../../domain/action-proposal'
import { todayBizDate } from '../../domain/date'

const mockBridge = vi.hoisted(() => ({
  insertTransaction: vi.fn().mockResolvedValue({ id: 'tx-1', version: 1 }),
  updateTransaction: vi.fn().mockResolvedValue({ id: 'tx-1', version: 2 }),
  listTransactionsByBook: vi.fn().mockResolvedValue([]),
  softDeleteTransaction: vi.fn().mockResolvedValue({ success: true }),
  triggerSync: vi.fn().mockResolvedValue(undefined),
  executeProposal: vi.fn(),
  validateProposal: vi.fn().mockResolvedValue({ valid: true }),
  interpretAI: vi.fn(),
  isAvailable: vi.fn().mockReturnValue(false),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

vi.mock('../../domain/nlu/parser', () => ({
  parseInput: vi.fn(),
}))

const TZ = 'Asia/Shanghai'

function txProposal(): ActionProposal {
  return {
    proposalId: 'prop-1',
    action: 'add_tx',
    confidence: 0.9,
    requiresConfirmation: true,
    message: '记下这笔',
    warnings: [],
    transactions: [{
      type: 'expense',
      amountCents: 1250,
      categoryId: 'cat-food',
      bizDate: todayBizDate(TZ),
      note: '午饭',
    }],
  }
}

const setup = () => renderHook(() =>
  useAssistant({ categories: [], bookId: 'book-1', defaultAccountId: null, timezone: TZ }))

describe('T14-4 聊天历史持久化 — use-assistant 接线', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.insertTransaction.mockResolvedValue({ id: 'tx-1', version: 1 })
    mockBridge.isAvailable.mockReturnValue(false)
    // store 是模块级单测共享态——逐用例复位防串
    useAssistantChatStore.setState({ messages: [] })
    localStorage.clear()
  })

  it('unmount→remount 后历史仍在（切页不丢）', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    const h1 = setup()
    await act(async () => { await h1.result.current.submitInput('午饭12.5') })
    const before = h1.result.current.messages
    expect(before).toHaveLength(2)
    expect(before[1].proposalStatus).toBe('committed')
    // 挂载期内存瞬时态：活动卡由 activeProposal 门控（T13-3 后无撤销窗 phase）
    expect(h1.result.current.phase).toBe('idle')
    expect(h1.result.current.activeProposal).not.toBeNull()
    h1.unmount()

    const h2 = setup()
    expect(h2.result.current.messages.map(m => m.text)).toEqual(before.map(m => m.text))
    expect(h2.result.current.messages[1].proposalStatus).toBe('committed')
  })

  it('恢复后瞬时态不复活：idle / 无活动卡（不出现可点击的过期撤销钮）', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    const h1 = setup()
    await act(async () => { await h1.result.current.submitInput('午饭12.5') })
    h1.unmount()

    const h2 = setup()
    expect(h2.result.current.phase).toBe('idle')
    expect(h2.result.current.activeProposal).toBeNull()
    expect(h2.result.current.cardStatus).toBeNull()
    expect(h2.result.current.pendingOp).toBeNull()
    // T16-2（REQ-58）：历史消息现由页面侧渲染只读账单卡（assistant-page-history-card.test.tsx）；
    // 可写路径（编辑/撤销）门控仍是内存态 activeProposal/activeTxIdsRef——恢复后不复活
  })

  it('clear（登出挂钩行为）后 remount 为空', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    const h1 = setup()
    await act(async () => { await h1.result.current.submitInput('午饭12.5') })
    h1.unmount()

    useAssistantChatStore.getState().clear()
    const h2 = setup()
    expect(h2.result.current.messages).toEqual([])
  })

  it('恢复后新消息继续追加在历史之后', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    const h1 = setup()
    await act(async () => { await h1.result.current.submitInput('午饭12.5') })
    h1.unmount()

    const h2 = setup()
    await act(async () => { await h2.result.current.submitInput('午饭12.5') })
    expect(h2.result.current.messages).toHaveLength(4)
  })
})
