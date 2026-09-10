/**
 * SearchPage 组件测试（T12-5 / REQ-33，明细页搜索迁移用例承接 + 新增覆盖）。
 *
 * 覆盖（卡验收）：
 * - 命中过滤：跨月份按备注/分类命中 + 命中数量行 + 清空恢复；无命中 → 空态「未找到相关记录」
 * - 账本范围：router state 带 bookId → 只查该账本；无 state → pickEntryBook 回退；
 *   master 不直查（LEAK 防呆），合并其余普通账本
 * - 全量语义：非当前月记录也直接列出（与明细页"仅当月"相反）+ 按 occurredAt 倒序
 * - 输入框自动聚焦；返回栏（PageHeader back）存在
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { SearchPage } from '../../features/ledger/SearchPage'
import type { BookData, CategoryData, TransactionData } from '../../platform/bridge/bridge-types'

const mockBridge = vi.hoisted(() => ({
  getMeta: vi.fn().mockResolvedValue({}),
  setMeta: vi.fn().mockResolvedValue({ success: true }),
  call: vi.fn(),
  softDeleteTransaction: vi.fn(),
  // T12-10：TxEditSheet 挂载时拉取（开层用例用）
  listBooks: vi.fn().mockResolvedValue([]),
  listAccounts: vi.fn().mockResolvedValue([]),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

const CATS: CategoryData[] = [
  { id: 'cat-food', kind: 'expense', name: '餐饮', parentId: null, version: 1 },
  { id: 'cat-salary', kind: 'income', name: '工资', parentId: null, version: 1 },
]

const BOOKS: BookData[] = [
  { id: 'm1', ownerId: 'o', name: '总账本', icon: '📊', version: 1, isMaster: true },
  { id: 'a', ownerId: 'o', name: '账本A', icon: '🅰️', version: 1 },
  { id: 'b', ownerId: 'o', name: '账本B', icon: '🅱️', version: 1 },
]

const mkTx = (over: Partial<TransactionData> & { id: string; bookId: string; bizDate: string }): TransactionData => ({
  type: 'expense',
  amountCents: 5500,
  categoryId: 'cat-food',
  accountId: null,
  note: '',
  occurredAt: new Date(`${over.bizDate}T12:00:00`).getTime(),
  source: 'manual',
  version: 1,
  deletedAt: null,
  ...over,
})

// 账本A：两个月各一笔（验证"全量、跨月份"）；账本B：一笔
const TXS_A: TransactionData[] = [
  mkTx({ id: 'tx-a-cur', bookId: 'a', bizDate: '2026-09-03', note: 'A的午饭' }),
  mkTx({ id: 'tx-a-prev', bookId: 'a', bizDate: '2026-08-05', type: 'income', amountCents: 800000, categoryId: 'cat-salary', note: 'A的八月工资' }),
]
const TXS_B: TransactionData[] = [
  mkTx({ id: 'tx-b', bookId: 'b', bizDate: '2026-08-20', note: 'B的打车' }),
]

function mockCalls(): string[] {
  return mockBridge.call.mock.calls.filter(c => c[0] === 'transaction.listByBook').map(c => JSON.stringify(c[1]))
}

const renderPage = (state?: { bookId: string } | null) =>
  render(
    <MemoryRouter initialEntries={[{ pathname: '/search', state }]}>
      <SearchPage />
    </MemoryRouter>,
  )

describe('SearchPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.call.mockImplementation((cmd: string, payload?: { bookId?: string }) => {
      if (cmd === 'book.list') return Promise.resolve(BOOKS)
      if (cmd === 'category.list') return Promise.resolve(CATS)
      if (cmd === 'transaction.listByBook') {
        if (payload?.bookId === 'a') return Promise.resolve(TXS_A)
        if (payload?.bookId === 'b') return Promise.resolve(TXS_B)
        // master 自身不存流水：若被查询说明实现走错分支
        if (payload?.bookId === 'm1') return Promise.resolve([{ ...TXS_A[0], id: 'LEAK', note: 'LEAKED' }])
        return Promise.resolve([])
      }
      return Promise.resolve([])
    })
  })

  it('无 state 回退默认账本口径：全量跨月列出、倒序、自动聚焦、含返回栏', async () => {
    renderPage()
    // 默认选中第一个非 master（账本A）
    await waitFor(() => expect(screen.getByText('A的午饭')).toBeInTheDocument())
    expect(mockBridge.call.mock.calls.filter(c => c[0] === 'transaction.listByBook').map(c => c[1]?.bookId)).toEqual(['a'])
    // 非当前月记录同样列出（区别于明细页仅当月），occurredAt 倒序 → 午饭(9月)在工资(8月)前
    expect(screen.getByText('A的八月工资')).toBeInTheDocument()
    const rows = screen.getAllByTestId('search-result-row')
    expect(rows[0]).toHaveTextContent('A的午饭')
    expect(rows[1]).toHaveTextContent('A的八月工资')
    // 输入框自动聚焦
    expect(screen.getByLabelText('搜索账单')).toHaveFocus()
    // PageHeader 返回栏
    expect(screen.getByLabelText('返回')).toBeInTheDocument()
  })

  it('命中过滤：跨月匹配备注/分类 + 命中数量行 + 清空恢复', async () => {
    const user = userEvent.setup()
    renderPage({ bookId: 'a' })
    await screen.findByText('A的午饭')
    await user.type(screen.getByLabelText('搜索账单'), '工资')
    await waitFor(() => expect(screen.getByText('A的八月工资')).toBeInTheDocument())
    expect(screen.getByText('找到 1 笔相关记录')).toBeInTheDocument()
    expect(screen.queryByText('A的午饭')).not.toBeInTheDocument()
    // 清空后恢复全量
    await user.click(screen.getByLabelText('清空搜索'))
    await waitFor(() => expect(screen.getByText('A的午饭')).toBeInTheDocument())
    expect(screen.getByText('A的八月工资')).toBeInTheDocument()
    expect(screen.queryByText(/笔相关记录/)).not.toBeInTheDocument()
  })

  it('无命中 → 空态「未找到相关记录」', async () => {
    const user = userEvent.setup()
    renderPage({ bookId: 'a' })
    await screen.findByText('A的午饭')
    await user.type(screen.getByLabelText('搜索账单'), '不存在关键词')
    await waitFor(() =>
      expect(within(screen.getByTestId('empty-state')).getByText('未找到相关记录')).toBeInTheDocument()
    )
  })

  it('账本范围：state 带 bookId 时只查该账本', async () => {
    renderPage({ bookId: 'b' })
    await waitFor(() => expect(screen.getByText('B的打车')).toBeInTheDocument())
    expect(screen.queryByText('A的午饭')).not.toBeInTheDocument()
    expect(screen.queryByText('LEAKED')).not.toBeInTheDocument()
    expect(mockCalls()).toEqual([JSON.stringify({ bookId: 'b' })])
  })

  it('master 账本不直查：合并其余普通账本', async () => {
    renderPage({ bookId: 'm1' })
    await waitFor(() => expect(screen.getByText('A的午饭')).toBeInTheDocument())
    expect(screen.getByText('A的八月工资')).toBeInTheDocument()
    expect(screen.getByText('B的打车')).toBeInTheDocument()
    expect(screen.queryByText('LEAKED')).not.toBeInTheDocument()
    const queried = mockBridge.call.mock.calls
      .filter(c => c[0] === 'transaction.listByBook')
      .map(c => c[1]?.bookId)
    expect(queried).toContain('a')
    expect(queried).toContain('b')
    expect(queried).not.toContain('m1')
  })

  it('结果行点击打开 TxEditSheet，携带该行数据（T12-10 / REQ-36）', async () => {
    const user = userEvent.setup()
    renderPage({ bookId: 'a' })
    await screen.findByText('A的午饭')
    await user.click(screen.getByText('A的午饭'))
    const sheet = await screen.findByTestId('tx-edit-sheet')
    expect(within(sheet).getByTestId('tx-edit-amount')).toHaveValue(55)
    expect(within(sheet).getByTestId('tx-edit-note')).toHaveValue('A的午饭')
  })

  it('账本无流水 → 空态「暂无记录」', async () => {
    mockBridge.call.mockImplementation((cmd: string) => {
      if (cmd === 'book.list') return Promise.resolve(BOOKS)
      if (cmd === 'category.list') return Promise.resolve(CATS)
      return Promise.resolve([]) // listByBook 恒空
    })
    renderPage({ bookId: 'a' })
    await waitFor(() =>
      expect(within(screen.getByTestId('empty-state')).getByText('暂无记录')).toBeInTheDocument()
    )
  })
})
