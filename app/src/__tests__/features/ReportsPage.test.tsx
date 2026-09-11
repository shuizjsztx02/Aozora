/**
 * ReportsPage 测试（T10-5 / REQ-21 + T10-6 / REQ-22 + T15-5 / REQ-54）。
 *
 * 数据层（buildDailySeries 纯函数）：非当月满月天数；T15-5 起当月亦满月（未来日零填充）；逐日零填充；净额。
 * X 轴（trendTickFormatter）：只渲染 1/5/10/15/20/25/月末，其余返回空串。
 * 报表筛选（T10-5）：默认当前账本；切月重新过滤；账本下拉过滤 master；切账本重新拉取。
 * 趋势卡（T10-6）：图例 toggle 显隐；全月零交易空态"本期暂无记账"。
 *
 * 约定：金额数字在概览「支出/结余」两格重复出现，且概览标题可能与期间标签同字，
 * 故「存在」用 getAllByText(...).length>0、「不存在」用 queryAllByText(...)（getAllBy 零匹配会抛）。
 * 就绪门用加载完成后唯一出现的 selectedBook 按钮文字「默认账本」。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { ReportsPage, buildDailySeries, trendTickFormatter } from '../../features/reports/ReportsPage'
import type { TransactionData, BookData, CategoryData } from '../../platform/bridge/bridge-types'

const mockBridge = vi.hoisted(() => ({
  call: vi.fn(),
  getMeta: vi.fn().mockResolvedValue({}),
  shareText: vi.fn(),
}))
vi.mock('../../platform/hooks/use-bridge', () => ({ useBridge: () => mockBridge }))
vi.mock('../../features/reports/AiInsightCard', () => ({
  AiInsightCard: () => <div data-testid="ai-insight" />,
}))
vi.mock('../../design/theme', () => ({
  useChartColors: () => ['#111111', '#222222', '#333333'],
}))

const NOW = new Date()
const curPeriod = `${NOW.getFullYear()}-${String(NOW.getMonth() + 1).padStart(2, '0')}`
const nextDate = new Date(NOW.getFullYear(), NOW.getMonth() + 1, 1)
const nextPeriod = `${nextDate.getFullYear()}-${String(nextDate.getMonth() + 1).padStart(2, '0')}`

const BOOKS: BookData[] = [
  { id: 'b1', ownerId: 'o', name: '默认账本', icon: '📒', version: 1 },
  { id: 'b2', ownerId: 'o', name: '旅游账本', icon: '✈️', version: 1 },
  { id: 'm', ownerId: 'o', name: '总账本', icon: '📊', version: 1, isMaster: true },
]
const CATS: CategoryData[] = [
  { id: 'c1', kind: 'expense', name: '餐饮', parentId: null, version: 1 },
]

const mkTx = (over: Partial<TransactionData> & { id: string; bookId: string; bizDate: string }): TransactionData => ({
  type: 'expense',
  amountCents: 1000,
  categoryId: 'c1',
  accountId: null,
  note: '',
  occurredAt: new Date(`${over.bizDate}T12:00:00`).getTime(),
  source: 'manual',
  version: 1,
  deletedAt: null,
  ...over,
})

// b1：当月 123.45 + 下月 678.90；b2：当月 99.00
const TXS_B1: TransactionData[] = [
  mkTx({ id: 't1', bookId: 'b1', bizDate: `${curPeriod}-03`, amountCents: 12345 }),
  mkTx({ id: 't2', bookId: 'b1', bizDate: `${nextPeriod}-05`, amountCents: 67890 }),
]
const TXS_B2: TransactionData[] = [
  mkTx({ id: 't3', bookId: 'b2', bizDate: `${curPeriod}-04`, amountCents: 9900 }),
]

const renderPage = () =>
  render(
    <MemoryRouter>
      <ReportsPage />
    </MemoryRouter>,
  )

const mockCalls = (txByBook: (id?: string) => TransactionData[]) => {
  mockBridge.getMeta.mockResolvedValue({})
  mockBridge.call.mockImplementation((cmd: string, args?: { bookId?: string }) => {
    if (cmd === 'book.list') return Promise.resolve(BOOKS)
    if (cmd === 'category.list') return Promise.resolve(CATS)
    if (cmd === 'budget.list') return Promise.resolve([])
    if (cmd === 'transaction.listByBook') return Promise.resolve(txByBook(args?.bookId))
    return Promise.resolve([])
  })
}

describe('buildDailySeries（T10-6 数据层）', () => {
  it('非当月 → 满月天数（2026-02 平年 28 天）', () => {
    const rows = buildDailySeries('2026-02', [])
    expect(rows).toHaveLength(28)
    expect(rows[0].date).toBe('02-01')
    expect(rows[27].date).toBe('02-28')
    expect(rows.every(r => r.expense === 0 && r.income === 0 && r.balance === 0)).toBe(true)
  })

  it('交易日按 DD 落位，未交易日保持零填充；净额 = income − expense', () => {
    const txs: TransactionData[] = [
      mkTx({ id: 'a', bookId: 'b', bizDate: '2026-04-05', type: 'expense', amountCents: 500 }),
      mkTx({ id: 'b', bookId: 'b', bizDate: '2026-04-05', type: 'income', amountCents: 800 }),
      mkTx({ id: 'c', bookId: 'b', bizDate: '2026-04-09', type: 'expense', amountCents: 1200 }),
    ]
    const rows = buildDailySeries('2026-04', txs)
    expect(rows).toHaveLength(30)
    expect(rows[4]).toMatchObject({ date: '04-05', expense: 500, income: 800, balance: 300 })
    expect(rows[8]).toMatchObject({ date: '04-09', expense: 1200, income: 0, balance: -1200 })
    expect(rows[0]).toMatchObject({ expense: 0, income: 0, balance: 0 })
  })

  it('当月（T15-5 / REQ-54）→ 满月横轴（不再截至今日）', () => {
    const rows = buildDailySeries(curPeriod, [])
    const [y, m] = curPeriod.split('-').map(Number)
    const expected = new Date(y, m, 0).getDate()
    expect(rows).toHaveLength(expected)
    expect(rows[0].day).toBe(1)
    expect(rows[rows.length - 1].day).toBe(expected)
  })

  it('trendTickFormatter：渲染 1/5/10/15/20/25/月末，其余空串', () => {
    // 平年二月：月末 = 28
    expect(trendTickFormatter('02-01')).toBe('1')
    expect(trendTickFormatter('02-05')).toBe('5')
    expect(trendTickFormatter('02-10')).toBe('10')
    expect(trendTickFormatter('02-15')).toBe('15')
    expect(trendTickFormatter('02-20')).toBe('20')
    expect(trendTickFormatter('02-25')).toBe('25')
    expect(trendTickFormatter('02-28')).toBe('28') // 月末
    expect(trendTickFormatter('02-03')).toBe('')
    expect(trendTickFormatter('02-17')).toBe('')
    // 31 天月：月末 = 31
    expect(trendTickFormatter('01-31')).toBe('31')
    expect(trendTickFormatter('01-17')).toBe('')
  })
})

describe('ReportsPage 期间 + 账本筛选（T10-5）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('默认第一个非 master 账本，只统计当月', async () => {
    mockCalls(id => (id === 'b2' ? TXS_B2 : TXS_B1))
    renderPage()
    await screen.findByText('默认账本') // 就绪门：加载完成后 selectedBook 按钮
    expect(mockBridge.call).toHaveBeenCalledWith('transaction.listByBook', { bookId: 'b1' })
    expect(screen.getAllByText(/123\.45/).length).toBeGreaterThan(0)
    expect(screen.queryAllByText(/678\.90/)).toHaveLength(0)
  })

  it('点"下个月"后按新 period 重新过滤', async () => {
    mockCalls(id => (id === 'b2' ? TXS_B2 : TXS_B1))
    renderPage()
    await screen.findByText('默认账本')
    fireEvent.click(screen.getByLabelText('下个月'))
    await waitFor(() => expect(screen.getAllByText(/678\.90/).length).toBeGreaterThan(0))
    expect(screen.queryAllByText(/123\.45/)).toHaveLength(0)
  })

  it('账本下拉过滤 master — 总账本不出现', async () => {
    mockCalls(id => (id === 'b2' ? TXS_B2 : TXS_B1))
    renderPage()
    await screen.findByText('默认账本')
    fireEvent.click(screen.getByLabelText('切换账本'))
    expect(await screen.findByText('旅游账本')).toBeInTheDocument()
    expect(screen.queryByText('总账本')).not.toBeInTheDocument()
  })

  it('切账本 B 只统计 B（按所选 bookId 重新拉取）', async () => {
    mockCalls(id => (id === 'b2' ? TXS_B2 : TXS_B1))
    renderPage()
    await screen.findByText('默认账本')
    fireEvent.click(screen.getByLabelText('切换账本'))
    fireEvent.click(await screen.findByText('旅游账本'))
    await waitFor(() =>
      expect(mockBridge.call).toHaveBeenCalledWith('transaction.listByBook', { bookId: 'b2' }),
    )
    await waitFor(() => expect(screen.getAllByText(/99\.00/).length).toBeGreaterThan(0))
    expect(screen.queryAllByText(/123\.45/)).toHaveLength(0)
  })
})

describe('ReportsPage 趋势卡（T10-6）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('折线模式三图例默认全开，点图例切换显隐（aria-pressed）', async () => {
    mockCalls(() => TXS_B1)
    renderPage()
    await screen.findByText('默认账本')
    expect(screen.getByTestId('trend-legend-expense')).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByTestId('trend-legend-expense'))
    expect(screen.getByTestId('trend-legend-expense')).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(screen.getByTestId('trend-legend-balance'))
    expect(screen.getByTestId('trend-legend-balance')).toHaveAttribute('aria-pressed', 'false')
  })

  it('模式切换：点"柱状"后曲线/柱状按钮仍并存', async () => {
    mockCalls(() => TXS_B1)
    renderPage()
    await screen.findByText('默认账本')
    fireEvent.click(screen.getByLabelText('柱状模式'))
    expect(screen.getByLabelText('折线模式')).toBeInTheDocument()
    expect(screen.getByLabelText('柱状模式')).toBeInTheDocument()
  })

  it('全月零交易仍渲染趋势卡 + 空态"本期暂无记账"', async () => {
    mockCalls(() => [])
    renderPage()
    expect(await screen.findByText('本期暂无记账')).toBeInTheDocument()
  })
})

describe('ReportsPage 日历视图（T11-3）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('默认图表态；切日历 → 图卡区隐藏、账本/期间行保留、同一数据源；再切回', async () => {
    mockCalls(() => TXS_B1)
    renderPage()
    await screen.findByText('默认账本')
    expect(screen.queryByTestId('calendar-view')).not.toBeInTheDocument()
    expect(screen.getByTestId('trend-legend-expense')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('toggle-calendar-view'))
    expect(await screen.findByTestId('calendar-view')).toBeInTheDocument()
    expect(screen.queryByTestId('trend-legend-expense')).not.toBeInTheDocument()
    expect(screen.queryByTestId('ai-insight')).not.toBeInTheDocument()
    // 账本 / 期间两行保留（共享 state，零新增桥调用）
    expect(screen.getByLabelText('切换账本')).toBeInTheDocument()
    expect(screen.getByLabelText('上个月')).toBeInTheDocument()
    // 与图表同一派生 txs：当月 3 日 123.45 支出 → 日历格红色调净额
    const d3 = screen.getByTestId('cal-day-3')
    expect(d3).toHaveAttribute('data-tone', 'expense')
    expect(d3).toHaveTextContent('-¥123.45')

    fireEvent.click(screen.getByTestId('toggle-calendar-view'))
    await waitFor(() => expect(screen.queryByTestId('calendar-view')).not.toBeInTheDocument())
    expect(screen.getByTestId('trend-legend-expense')).toBeInTheDocument()
  })
})
