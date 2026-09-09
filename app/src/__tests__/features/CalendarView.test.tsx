/**
 * CalendarView 测试（T11-3 / REQ-25 决策 #6）。
 *
 * 逐日分桶净额与着色 tone；网定格（当月天数全可点）；今天描边 + 默认选中；
 * 点选日切流水（分类名/金额/备注副行）；月度小结三值；空月文案。
 * 纯展示组件——数据由 props 传入，无 bridge mock。
 */
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { CalendarView } from '../../features/reports/CalendarView'
import type { TransactionData, CategoryData } from '../../platform/bridge/bridge-types'

const NOW = new Date()
const curPeriod = `${NOW.getFullYear()}-${String(NOW.getMonth() + 1).padStart(2, '0')}`

const CATS: CategoryData[] = [
  { id: 'c1', kind: 'expense', name: '餐饮', parentId: null, version: 1 },
  { id: 'c2', kind: 'income', name: '工资', parentId: null, version: 1 },
]

const mkTx = (over: Partial<TransactionData> & { id: string; bizDate: string }): TransactionData => ({
  bookId: 'b1',
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

describe('CalendarView 逐日分桶（网格 + 净额着色）', () => {
  // 2026-04：5 日 支出5.00+收入8.00（净额 +3.00 income 调）；9 日 支出12.00（-12.00 expense 调）
  const TXS = [
    mkTx({ id: 'a', bizDate: '2026-04-05', type: 'expense', amountCents: 500 }),
    mkTx({ id: 'b', bizDate: '2026-04-05', type: 'income', amountCents: 800, categoryId: 'c2' }),
    mkTx({ id: 'c', bizDate: '2026-04-09', type: 'expense', amountCents: 1200 }),
  ]

  it('净额正/负日 → tone 与浅底 token、净额文字', () => {
    render(<CalendarView txs={TXS} categories={CATS} period="2026-04" />)
    const d5 = screen.getByTestId('cal-day-5')
    expect(d5).toHaveAttribute('data-tone', 'income')
    expect(d5).toHaveTextContent('+¥3.00')
    expect(d5.querySelector('[data-tone-bg]')?.getAttribute('style')).toContain('var(--income)')

    const d9 = screen.getByTestId('cal-day-9')
    expect(d9).toHaveAttribute('data-tone', 'expense')
    expect(d9).toHaveTextContent('-¥12.00')
    expect(d9.querySelector('[data-tone-bg]')?.getAttribute('style')).toContain('var(--expense)')
  })

  it('有交易但收支相抵日 → tone=zero 无浅底、净额 ¥0.00', () => {
    const txs = [
      mkTx({ id: 'a', bizDate: '2026-04-20', type: 'expense', amountCents: 300 }),
      mkTx({ id: 'b', bizDate: '2026-04-20', type: 'income', amountCents: 300, categoryId: 'c2' }),
    ]
    render(<CalendarView txs={txs} categories={CATS} period="2026-04" />)
    const d20 = screen.getByTestId('cal-day-20')
    expect(d20).toHaveAttribute('data-tone', 'zero')
    expect(d20).toHaveTextContent('¥0.00')
    expect(d20.querySelector('[data-tone-bg]')).toBeNull()
  })

  it('无交易日透明无金额；4 月 30 天全为可点格', () => {
    render(<CalendarView txs={TXS} categories={CATS} period="2026-04" />)
    const d15 = screen.getByTestId('cal-day-15')
    expect(d15).toHaveAttribute('data-tone', 'none')
    expect(d15).not.toHaveTextContent('¥')
    expect(document.querySelectorAll('[data-testid^="cal-day-"]')).toHaveLength(30)
  })
})

describe('CalendarView 选中日 / 小结 / 空态', () => {
  const TXS = [
    mkTx({ id: 'a', bizDate: '2026-04-05', type: 'expense', amountCents: 500, note: '午饭' }),
    mkTx({ id: 'b', bizDate: '2026-04-09', type: 'income', amountCents: 80000, categoryId: 'c2' }),
  ]

  it('月度小结 = 收入/支出/结余三值', () => {
    render(<CalendarView txs={TXS} categories={CATS} period="2026-04" />)
    const summary = screen.getByTestId('calendar-summary')
    expect(within(summary).getByText('¥5.00')).toBeInTheDocument()
    expect(within(summary).getByText('¥800.00')).toBeInTheDocument()
    expect(within(summary).getByText('¥795.00')).toBeInTheDocument()
  })

  it('历史月默认选中 1 日（今天不在该月）', () => {
    render(<CalendarView txs={TXS} categories={CATS} period="2026-04" />)
    expect(screen.getByTestId('cal-day-1')).toHaveAttribute('aria-pressed', 'true')
  })

  it('当月默认选中今天，今天格 data-today 描边', () => {
    const todayTx = mkTx({ id: 't0', bizDate: `${curPeriod}-${String(NOW.getDate()).padStart(2, '0')}`, amountCents: 6600, note: '今天一笔' })
    render(<CalendarView txs={[todayTx]} categories={CATS} period={curPeriod} />)
    const todayCell = screen.getByTestId(`cal-day-${NOW.getDate()}`)
    expect(todayCell).toHaveAttribute('data-today', 'true')
    expect(todayCell).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByTestId('calendar-day-list')).toHaveTextContent('今天一笔')
  })

  it('点选 5 日 → 流水行 = 分类名 + 符号金额 + 时间·备注副行', () => {
    render(<CalendarView txs={TXS} categories={CATS} period="2026-04" />)
    fireEvent.click(screen.getByTestId('cal-day-5'))
    expect(screen.getByTestId('cal-day-5')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByTestId('cal-day-1')).toHaveAttribute('aria-pressed', 'false')
    const list = screen.getByTestId('calendar-day-list')
    expect(within(list).getByText('餐饮')).toBeInTheDocument()
    expect(within(list).getByText('-¥5.00')).toBeInTheDocument()
    expect(within(list).getByText('12:00 · 午饭')).toBeInTheDocument()
  })

  it('选中日无交易 → "当日暂无交易"', () => {
    render(<CalendarView txs={TXS} categories={CATS} period="2026-04" />)
    fireEvent.click(screen.getByTestId('cal-day-20'))
    expect(screen.getByText('当日暂无交易')).toBeInTheDocument()
  })

  it('空月 → 沿用现有文案"本期暂无记账"', () => {
    render(<CalendarView txs={[]} categories={CATS} period="2026-04" />)
    expect(screen.getByText('本期暂无记账')).toBeInTheDocument()
  })
})
