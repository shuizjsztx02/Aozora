/**
 * HomePage 测试 — T10-8 收口补测（REQ-17 / 决策 #1）。
 *
 * 预算进度"已用 N%"只统计 includeInBudget !== false 的支出；
 * "本月总额"不过滤（含不计入项）；旧数据缺省字段=计入。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { HomePage } from '../../features/ledger/HomePage'
import { todayBizDate } from '../../domain/date'
import type { TransactionData } from '../../platform/bridge/bridge-types'

const mockBridge = vi.hoisted(() => ({
  call: vi.fn(),
  getMeta: vi.fn().mockResolvedValue({}),
  isAvailable: () => true,
  // T12-10：TxEditSheet 挂载时拉取（开层用例用）
  listBooks: vi.fn().mockResolvedValue([]),
  listAccounts: vi.fn().mockResolvedValue([]),
}))
vi.mock('../../platform/hooks/use-bridge', () => ({ useBridge: () => mockBridge }))
vi.mock('../../components/SyncBadge', () => ({
  SyncBadge: () => <span data-testid="sync-badge" />,
}))

const curMonth = todayBizDate('Asia/Shanghai').slice(0, 7)

const mkTx = (over: Partial<TransactionData> & { id: string }): TransactionData => ({
  bookId: 'b1',
  type: 'expense',
  amountCents: 1000,
  categoryId: 'c1',
  accountId: null,
  note: '',
  occurredAt: Date.now(),
  source: 'manual',
  version: 1,
  deletedAt: null,
  bizDate: `${curMonth}-03`,
  ...over,
})

describe('HomePage 预算进度过滤（REQ-17 决策 #1）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.getMeta.mockResolvedValue({})
  })

  function setup(txs: TransactionData[], budgetCents: number) {
    mockBridge.call.mockImplementation((cmd: string) => {
      if (cmd === 'book.list') return Promise.resolve([{ id: 'b1', name: '默认账本', version: 1 }])
      if (cmd === 'category.list') return Promise.resolve([{ id: 'c1', kind: 'expense', name: '餐饮', parentId: null, version: 1 }])
      if (cmd === 'budget.list') return Promise.resolve([{ amountCents: budgetCents, period: curMonth }])
      if (cmd === 'transaction.listByBook') return Promise.resolve(txs)
      return Promise.resolve([])
    })
  }

  it('已用% 只含计入项；本月总额含全部（不计入项也在）', async () => {
    setup(
      [
        mkTx({ id: 'a', amountCents: 10000, includeInBudget: true }),
        mkTx({ id: 'b', amountCents: 5000, includeInBudget: false }),
        mkTx({ id: 'c', amountCents: 300 }), // 缺省字段 = 计入
      ],
      100000,
    )
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )
    // 计入 = 10000+300 = 10300/100000 = 10.3% → round 10%
    await waitFor(() => expect(screen.getByText(/已用 10%/)).toBeInTheDocument())
    // 总额 = 15300 → ¥153.00（不过滤）
    expect(screen.getByText(/¥153\.00/)).toBeInTheDocument()
  })

  it('旧数据无 includeInBudget 字段按计入处理', async () => {
    setup([mkTx({ id: 'a', amountCents: 4000 })], 10000)
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText(/已用 40%/)).toBeInTheDocument())
  })

  it('今日流水行点击打开 TxEditSheet，金额预填自行数据（T12-10 / REQ-36）', async () => {
    const today = todayBizDate('Asia/Shanghai')
    setup([mkTx({ id: 't1', bizDate: today, amountCents: 5500 })], 100000)
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('餐饮')).toBeInTheDocument())
    fireEvent.click(screen.getByText('餐饮'))
    const sheet = await screen.findByTestId('tx-edit-sheet')
    expect(within(sheet).getByTestId('tx-edit-amount')).toHaveValue(55)
  })
})
