/**
 * ReportsPage — 报表页。
 *
 * 月度统计、分类占比环图、每日趋势、预算执行、环比。
 * 数据来源：Room + domain/aggregator.ts（纯本地）。
 *
 * 第十轮 REQ-21（T10-5）：期间切换（复用 PeriodPanel）+ 账本下拉。
 * period / bookId 为 state，月度概览 / AI 洞察 / 分享文案 / 预算 / 趋势全部跟随。
 *
 * T16-6（REQ-62）：账本下拉恢复总账本——选中即「全部账本」聚合视图（桥侧 masterId 走聚合），
 * 聚合视图不渲染预算卡（预算挂在具体账本上，不发明跨账本预算）。
 *
 * 第十一轮 REQ-25（T11-3，决策 #6）：📅 切换 chart/calendar 两态（T15-4 起钮在账本行右侧）；
 * calendar 态隐藏图卡区、共享 period/bookId state 与派生 txs——零新增桥调用。
 */

import { useState, useEffect, useCallback, useMemo } from 'react'
import { EmptyState } from '../../components/StateViews'
import { PieChart, Pie, Cell, LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { useBridge } from '../../platform/hooks/use-bridge'
import { groupByCategory, sumTransactions } from '../../domain/aggregator'
import * as Money from '../../domain/money'
import type { TransactionData, CategoryData, BookData } from '../../platform/bridge/bridge-types'
import { toDomainTransaction } from '../../domain/types'
import { pickEntryBook } from '../../domain/books'
import { CategoryIcon } from '../../components/CategoryIcon'
import { PeriodPanel } from '../../components/PeriodPanel'
import { useChartColors } from '../../design/theme'
import { AiInsightCard } from './AiInsightCard'
import { CalendarView } from './CalendarView'

function formatCents(cents: number): string {
  return Money.formatDisplay(Money.cents(cents))
}

/**
 * T16-1（REQ-57）：按点击横坐标反推最近数据点。
 * recharts 3 的 `activeTooltipIndex` 是字符串且依赖 hover 态（触屏不可靠），故改由点击坐标自算。
 * 两图轴配置固定（内建边距 5 / 隐藏 YAxis 零宽 / XAxis 满宽），绘制区 x = [5, width - 5]。
 */
export function nearestTrendIndex(clickX: number, rectWidth: number, count: number): number | null {
  if (count <= 0 || !Number.isFinite(clickX) || !Number.isFinite(rectWidth) || rectWidth <= 0) return null
  const left = Math.min(5, rectWidth / 2)
  const plotWidth = rectWidth - left * 2
  const raw = Math.floor((((clickX - left) / plotWidth) * count))
  return Math.max(0, Math.min(count - 1, raw))
}

/** 点击处以图表容器左边为原点的横坐标（recharts 事件 currentTarget 即图表容器） */
function chartClickX(event: { currentTarget?: EventTarget | null; nativeEvent?: { clientX?: number } }): number | null {
  const rect = event.currentTarget instanceof Element ? event.currentTarget.getBoundingClientRect() : null
  const clientX = event.nativeEvent?.clientX
  if (!rect || typeof clientX !== 'number') return null
  return clientX - rect.left
}

function currentPeriod(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

function periodLabel(period: string): string {
  const [y, m] = period.split('-')
  return `${y}年${parseInt(m)}月`
}

function shiftMonth(period: string, delta: number): string {
  const [y, m] = period.split('-').map(Number)
  const d = new Date(y, m - 1 + delta, 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

/** budget.list 返回行（本页面消费的最小字段集） */
interface BudgetRow {
  amountCents: number
  period: string
  budgetType: string
  categoryId: string | null
}

/** T10-6（REQ-22 决策 #7）：趋势卡三系列键 + 配色（结余走新 --balance token） */
type SeriesKey = 'expense' | 'income' | 'balance'
const SERIES_COLOR: Record<SeriesKey, string> = {
  expense: 'var(--expense)',
  income: 'var(--income)',
  balance: 'var(--balance)',
}
const SERIES_LABEL: Record<SeriesKey, string> = {
  expense: '支出',
  income: '收入',
  balance: '结余',
}
/** 逐日零填充数据点 */
interface TrendPoint {
  date: string   // MM-DD（X 轴）
  ymd: string     // YYYY-MM-DD（浮层展示）
  day: number
  expense: number
  income: number
  balance: number
}

/**
 * 趋势图 X 轴刻度过滤（T15-5 / REQ-54）：interval=0 保证首末刻度必现，
 * formatter 只渲染 1/5/10/15/20/25/月末，其余返回空串避免重叠。
 */
export function trendTickFormatter(date: string): string {
  const day = parseInt(date.slice(-2), 10)
  if (day === 1 || day === 5 || day === 10 || day === 15 || day === 20 || day === 25) return String(day)
  // 月末：date = "MM-DD"，当月天数即 DD 字符串对应日期减一天后的月内日
  const mm = parseInt(date.slice(0, 2), 10)
  // 用平年构造日期取月末，避免传入年份缺失；仅当 day == 月末才显示
  const lastDay = new Date(2023, mm, 0).getDate()
  if (day === lastDay) return String(lastDay)
  return ''
}

/**
 * period 全月逐日零填充（T10-6 / REQ-22 决策 #7；T15-5 / REQ-54 满月横轴）。
 * 无论是否当月，均铺 1→月末（未来日交易量为 0，横轴仍满格）。
 * txs 须已按 period 过滤（调用方保证）——按 bizDate 的 DD 落入对应日。
 */
export function buildDailySeries(period: string, txs: TransactionData[]): TrendPoint[] {
  const [y, m] = period.split('-').map(Number)
  const lastDay = new Date(y, m, 0).getDate()
  const mm = String(m).padStart(2, '0')
  const rows: TrendPoint[] = Array.from({ length: lastDay }, (_, i) => {
    const dd = String(i + 1).padStart(2, '0')
    return { date: `${mm}-${dd}`, ymd: `${y}-${mm}-${dd}`, day: i + 1, expense: 0, income: 0, balance: 0 }
  })
  for (const tx of txs) {
    const idx = parseInt(tx.bizDate.slice(8), 10) - 1  // txs 已按 period 过滤 → DD
    if (idx < 0 || idx >= rows.length) continue
    if (tx.type === 'expense') rows[idx].expense += tx.amountCents
    else if (tx.type === 'income') rows[idx].income += tx.amountCents
  }
  for (const r of rows) r.balance = r.income - r.expense
  return rows
}

export function ReportsPage() {
  const bridge = useBridge()
  const chartColors = useChartColors()
  const [period, setPeriod] = useState(currentPeriod)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerYear, setPickerYear] = useState(() => new Date().getFullYear())
  const [bookId, setBookId] = useState<string | null>(null)
  const [books, setBooks] = useState<BookData[]>([])
  const [bookPickerOpen, setBookPickerOpen] = useState(false)
  const [allTxs, setAllTxs] = useState<TransactionData[]>([])
  const [categories, setCategories] = useState<CategoryData[]>([])
  const [budgets, setBudgets] = useState<BudgetRow[]>([])
  const [tab, setTab] = useState<'expense' | 'income'>('expense')
  const [loading, setLoading] = useState(true)
  // T11-3（REQ-25，决策 #6）：图表/日历两态；两态共享 period/bookId state 与派生 txs
  const [view, setView] = useState<'chart' | 'calendar'>('chart')

  // T10-6（REQ-22）趋势卡独立控件：折线三系列显隐 / 柱状单选 / 逐点弹框。
  // 收支胶囊 tab 仅继续控制分类占比环图，不再控制趋势卡（决策 #7）。
  const [trendMode, setTrendMode] = useState<'line' | 'bar'>('line')
  const [visible, setVisible] = useState<Record<SeriesKey, boolean>>({ expense: true, income: true, balance: true })
  const [barSeries, setBarSeries] = useState<SeriesKey>('expense')
  const [activeDay, setActiveDay] = useState<TrendPoint | null>(null)

  // 账本流水按 bookId 加载（全量存 state，period 过滤派生，切月不重拉）
  const loadTxsForBook = useCallback(async (id: string) => {
    const [allTxs, budgets] = await Promise.all([
      bridge.call('transaction.listByBook', { bookId: id }) as Promise<TransactionData[]>,
      bridge.call('budget.list', { bookId: id }) as Promise<BudgetRow[]>,
    ])
    setAllTxs(allTxs.filter(tx => tx.deletedAt === null))
    setBudgets(budgets)
  }, [bridge])

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      // 决策 #2 / T7-6：初始展示目标 = 非 master 账本（记账目标语义）；T9-5b（决策 #3）default_book_id 优先
      const [bs, cats, meta] = await Promise.all([
        bridge.call('book.list', {}) as Promise<BookData[]>,
        bridge.call('category.list', {}) as Promise<CategoryData[]>,
        bridge.getMeta(['default_book_id']).catch(() => ({}) as Record<string, string | null>),
      ])
      setBooks(bs)
      setCategories(cats)
      const id = pickEntryBook(bs, meta['default_book_id'] ?? null)?.id ?? null
      setBookId(id)
      if (!id) {
        setAllTxs([])
        setBudgets([])
      } else {
        await loadTxsForBook(id)
      }
    } catch (err) {
      console.error('Failed to load reports:', err)
    } finally {
      setLoading(false)
    }
  }, [bridge, loadTxsForBook])

  useEffect(() => { loadData() }, [loadData])

  const handleSelectBook = async (id: string) => {
    setBookPickerOpen(false)
    setBookId(id)
    setLoading(true)
    try {
      await loadTxsForBook(id)
    } catch (err) {
      console.error('Failed to load book:', err)
    } finally {
      setLoading(false)
    }
  }

  // period 派生：切月只重过滤，不重新拉取
  const txs = useMemo(
    () => allTxs.filter(tx => tx.bizDate.startsWith(period)),
    [allTxs, period],
  )

  const selectedBook = books.find(b => b.id === bookId)
  // T16-6（REQ-62 / 决策 #8）：总账本聚合视图——预算挂在具体账本上，聚合视图不渲染预算卡（含分享文案中的预算进度）
  const isMasterView = !!selectedBook?.isMaster

  const budget = useMemo(() => {
    if (isMasterView) return null
    const mb = budgets.find(b => b.period === period && b.budgetType === 'monthly' && !b.categoryId)
    return mb ? { amountCents: mb.amountCents } : null
  }, [budgets, period, isMasterView])

  // 统计
  const stats = sumTransactions(txs.map(toDomainTransaction))

  // 第十轮 REQ-17（决策 #1）：预算执行卡只统计"计入预算"的支出（!== false 口径）；
  // 页面其余总额/趋势/占比不受该标记影响。
  const budgetSpentCents = txs
    .filter(tx => tx.type === 'expense' && tx.includeInBudget !== false)
    .reduce((sum, tx) => sum + tx.amountCents, 0)

  // 分类占比
  const categoryData = groupByCategory(txs.map(toDomainTransaction), tab).map(b => {
    const cat = categories.find(c => c.id === b.categoryId)
    return {
      name: cat?.name || '未分类',
      value: b.amount as number,
      count: b.count,
    }
  }).sort((a, b) => b.value - a.value)

  // 每日趋势（T10-6 / REQ-22 决策 #7）：逐日零填充；从收支 tab 解耦——tab 仅控制上方分类环图。
  const daily = useMemo(() => buildDailySeries(period, txs), [period, txs])

  return (
    <div className="min-h-screen">
      {/* ─── 账本切换（REQ-21 / T10-5；T16-6 / REQ-62：总账本 = 聚合视图） ───
          T15-4（REQ-53）：📅/📤 两钮自 PageHeader 挪来本行右侧，PageHeader 整段删除。 */}
      <div className="relative px-4 pt-3">
        <div className="flex items-center justify-between">
          <button
            onClick={() => setBookPickerOpen(v => !v)}
            className="flex items-center gap-1 text-base font-semibold active:opacity-60"
            style={{ color: 'var(--ink)' }}
            aria-label="切换账本"
            aria-expanded={bookPickerOpen}
          >
            {selectedBook && <CategoryIcon icon={selectedBook.icon} name={selectedBook.name} fallback="📒" className="text-lg" />}
            <span>{selectedBook?.name ?? '选择账本'}</span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.5 }}><polyline points="6 9 12 15 18 9" /></svg>
          </button>

          <div className="flex items-center">
            <button
              onClick={() => setView(v => (v === 'chart' ? 'calendar' : 'chart'))}
              className="px-2 text-lg"
              aria-label="切换日历视图"
              data-testid="toggle-calendar-view"
            >
              📅
            </button>
            <button
              onClick={() => {
                const month = period.replace('-', '年') + '月'
                const topCat = categoryData[0]
                const lines = [
                  `【Aozora ${month}记账统计】`,
                  `支出：${formatCents(stats.totalExpense as number)}`,
                  `收入：${formatCents(stats.totalIncome as number)}`,
                  `结余：${Money.formatSigned(stats.balance)}`,
                ]
                if (topCat) {
                  lines.push(`最大支出分类：${topCat.name}（${formatCents(topCat.value)}）`)
                }
                if (budget) {
                  const spent = stats.totalExpense as number
                  const pct = Math.round((spent / budget.amountCents) * 100)
                  lines.push(`预算进度：${pct}%（${formatCents(spent)} / ${formatCents(budget.amountCents)}）`)
                }
                bridge.shareText(lines.join('\n'), `Aozora ${month}统计`)
              }}
              className="px-2 text-lg"
              aria-label="分享"
            >
              📤
            </button>
          </div>
        </div>

        {bookPickerOpen && (
          <>
            <div className="fixed inset-0 z-10" onClick={() => setBookPickerOpen(false)} aria-hidden />
            <div
              className="glass-card absolute left-4 right-4 top-full z-20 mt-1 p-2"
              style={{ boxShadow: 'var(--card-shadow)' }}
            >
              {books.map(b => {
                const active = b.id === bookId
                return (
                  <button
                    key={b.id}
                    onClick={() => handleSelectBook(b.id)}
                    className="flex w-full items-center gap-2 rounded-lg px-3 py-2.5 text-left text-[15px] font-medium transition-colors"
                    style={{
                      background: active ? 'var(--accent)' : 'transparent',
                      color: active ? 'white' : 'var(--ink)',
                    }}
                  >
                    <CategoryIcon icon={b.icon} name={b.name} fallback="📒" />
                    <span className="flex-1 truncate">{b.name}</span>
                  </button>
                )
              })}
            </div>
          </>
        )}
      </div>

      {/* ─── 期间行：‹ 2026年9月 › + 年月选择面板（REQ-21，复用 PeriodPanel） ─── */}
      <div className="relative px-4">
        <div
          className="flex items-center justify-between py-2"
          style={{ borderBottom: '1px solid var(--glass-border)' }}
        >
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPeriod(shiftMonth(period, -1))}
              className="flex items-center justify-center w-8 h-8 rounded-full active:opacity-60"
              style={{ color: 'var(--ink)' }}
              aria-label="上个月"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
            </button>
            <button
              onClick={() => { setPickerYear(parseInt(period)); setPickerOpen(v => !v) }}
              className="flex items-center gap-1 px-2 py-1 text-[17px] font-semibold active:opacity-60"
              style={{ color: 'var(--ink)' }}
              aria-label="切换年份和月份"
            >
              {periodLabel(period)}
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.5 }}><polyline points="6 9 12 15 18 9" /></svg>
            </button>
            <button
              onClick={() => setPeriod(shiftMonth(period, 1))}
              className="flex items-center justify-center w-8 h-8 rounded-full active:opacity-60"
              style={{ color: 'var(--ink)' }}
              aria-label="下个月"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
            </button>
          </div>
        </div>

        <PeriodPanel
          open={pickerOpen}
          onClose={() => setPickerOpen(false)}
          year={pickerYear}
          onYearChange={setPickerYear}
          period={period}
          onPick={(p) => { setPeriod(p); setPickerOpen(false) }}
        />
      </div>

      <div className="px-4 py-4 space-y-4 pb-20">
        {loading ? (
          <div className="flex justify-center py-12">
            <div className="w-8 h-8 rounded-full border-2 border-t-transparent animate-spin" style={{ borderColor: 'var(--accent)', borderTopColor: 'transparent' }} />
          </div>
        ) : view === 'calendar' ? (
          /* T11-3（REQ-25，决策 #6）：日历态隐藏图卡区，共享 period/bookId 与派生 txs */
          <CalendarView txs={txs} categories={categories} period={period} />
        ) : (
          <>
            {/* 月度概览 */}
            <div className="glass-card p-4">
              <div className="text-sm text-ink2 mb-2">{period.replace('-', '年')}月</div>
              <div className="grid grid-cols-3 gap-4 text-center">
                <div>
                  <div className="text-xs text-ink3">支出</div>
                  <div className="money-num text-sm text-expense">{formatCents(stats.totalExpense as number)}</div>
                </div>
                <div>
                  <div className="text-xs text-ink3">收入</div>
                  <div className="money-num text-sm text-income">{formatCents(stats.totalIncome as number)}</div>
                </div>
                <div>
                  <div className="text-xs text-ink3">结余</div>
                  <div className="money-num text-sm" style={{ color: 'var(--accent)' }}>{Money.formatSigned(stats.balance)}</div>
                </div>
              </div>
            </div>

            {/* AI 洞察 */}
            <AiInsightCard bridge={bridge} bookId={bookId} period={period} localCount={txs.length} />

            {/* 收支切换 */}
            <div className="flex gap-2">
              {(['expense', 'income'] as const).map(t => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className="flex-1 py-2 rounded-lg text-sm font-medium"
                  style={{
                    background: tab === t ? (t === 'expense' ? 'var(--expense)' : 'var(--income)') : 'var(--glass)',
                    color: tab === t ? 'white' : 'var(--ink2)',
                  }}
                >
                  {t === 'expense' ? '支出' : '收入'}
                </button>
              ))}
            </div>

            {/* 分类占比环图 */}
            {categoryData.length > 0 ? (
              <div className="glass-card p-4">
                <h3 className="text-sm font-medium mb-3" style={{ color: 'var(--ink)' }}>分类占比</h3>
                <div className="h-48">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={categoryData}
                        cx="50%"
                        cy="50%"
                        innerRadius={40}
                        outerRadius={70}
                        dataKey="value"
                        stroke="none"
                      >
                        {categoryData.map((_, i) => (
                          <Cell key={i} fill={chartColors[i % chartColors.length]} />
                        ))}
                      </Pie>
                      <Tooltip
                        formatter={(value) => formatCents(Number(value))}
                        contentStyle={{ background: 'var(--glass)', border: '1px solid var(--glass-border)', borderRadius: 8 }}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                {/* 图例 */}
                <div className="grid grid-cols-2 gap-2 mt-2">
                  {categoryData.slice(0, 6).map((item, i) => (
                    <div key={item.name} className="flex items-center gap-2 text-xs">
                      <div className="w-2 h-2 rounded-full" style={{ background: chartColors[i % chartColors.length] }} />
                      <span className="text-ink2 truncate">{item.name}</span>
                      <span className="money-num text-ink3 ml-auto">{formatCents(item.value)}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div className="glass-card p-4">
                <EmptyState icon="📊" title="暂无数据" description="记账后这里会显示统计" />
              </div>
            )}

            {/* 每日趋势（T10-6 / REQ-22 决策 #7）：折线三系列 + 柱状单选 + 逐点弹框 */}
            <div className="glass-card p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-medium" style={{ color: 'var(--ink)' }}>每日趋势</h3>
                <div className="flex gap-1">
                  {(['line', 'bar'] as const).map(mode => (
                    <button
                      key={mode}
                      onClick={() => { setTrendMode(mode); setActiveDay(null) }}
                      className="px-2 py-1 rounded-md text-[11px] font-medium transition-colors"
                      style={{
                        background: trendMode === mode ? 'var(--accent)' : 'var(--glass)',
                        color: trendMode === mode ? 'white' : 'var(--ink2)',
                      }}
                      aria-label={mode === 'line' ? '折线模式' : '柱状模式'}
                    >
                      {mode === 'line' ? '曲线' : '柱状'}
                    </button>
                  ))}
                </div>
              </div>

              <div className="h-40 relative" data-testid="trend-chart">
                <ResponsiveContainer width="100%" height="100%">
                  {trendMode === 'line' ? (
                    <LineChart
                      data={daily}
                      onClick={(state, event) => {
                        // T16-1（REQ-57）：按点击坐标反推最近数据点；坐标不可得时回退 activeTooltipIndex
                        const rect = event.currentTarget.getBoundingClientRect()
                        const x = chartClickX(event)
                        // activeTooltipIndex 为字符串索引（recharts 3），仅作兜底
                        const i = x !== null ? nearestTrendIndex(x, rect.width, daily.length) : Number(state?.activeTooltipIndex)
                        setActiveDay(i === null || Number.isNaN(i) ? null : (daily[i] ?? null))
                      }}
                    >
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'var(--ink3)' }} interval={0} tickFormatter={trendTickFormatter} axisLine={false} tickLine={false} />
                      <YAxis hide />
                      {visible.expense && <Line type="monotone" dataKey="expense" name="支出" stroke={SERIES_COLOR.expense} strokeWidth={2} dot={{ r: 2, strokeWidth: 0 }} activeDot={{ r: 4 }} isAnimationActive={false} />}
                      {visible.income && <Line type="monotone" dataKey="income" name="收入" stroke={SERIES_COLOR.income} strokeWidth={2} dot={{ r: 2, strokeWidth: 0 }} activeDot={{ r: 4 }} isAnimationActive={false} />}
                      {visible.balance && <Line type="monotone" dataKey="balance" name="结余" stroke={SERIES_COLOR.balance} strokeWidth={2} dot={{ r: 2, strokeWidth: 0 }} activeDot={{ r: 4 }} isAnimationActive={false} />}
                    </LineChart>
                  ) : (
                    <BarChart
                      data={daily}
                      onClick={(state, event) => {
                        const rect = event.currentTarget.getBoundingClientRect()
                        const x = chartClickX(event)
                        const i = x !== null ? nearestTrendIndex(x, rect.width, daily.length) : Number(state?.activeTooltipIndex)
                        setActiveDay(i === null || Number.isNaN(i) ? null : (daily[i] ?? null))
                      }}
                    >
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'var(--ink3)' }} interval={0} tickFormatter={trendTickFormatter} axisLine={false} tickLine={false} />
                      <YAxis hide />
                      <Bar
                        dataKey={barSeries}
                        name={SERIES_LABEL[barSeries]}
                        fill={SERIES_COLOR[barSeries]}
                        isAnimationActive={false}
                        onClick={(data) => {
                          const p = (data as { payload?: TrendPoint })?.payload
                          if (p) setActiveDay(p)
                        }}
                      />
                    </BarChart>
                  )}
                </ResponsiveContainer>

                {/* 逐点弹框浮层 */}
                {activeDay && (
                  <div
                    className="glass-card absolute top-1 right-1 p-2 text-[11px]"
                    style={{ boxShadow: 'var(--card-shadow)' }}
                    data-testid="trend-callout"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-semibold" style={{ color: 'var(--ink)' }}>{activeDay.ymd}</span>
                      <button onClick={() => setActiveDay(null)} aria-label="关闭" style={{ color: 'var(--ink3)' }}>✕</button>
                    </div>
                    <div className="mt-1 space-y-0.5">
                      {trendMode === 'line' ? (
                        (['expense', 'income', 'balance'] as SeriesKey[])
                          .filter(k => visible[k])
                          .map(k => (
                            <div key={k} className="flex items-center justify-between gap-3">
                              <span style={{ color: SERIES_COLOR[k] }}>{SERIES_LABEL[k]}</span>
                              <span className="money-num" style={{ color: SERIES_COLOR[k] }}>
                                {k === 'balance' && activeDay.balance < 0 ? '-' : ''}{formatCents(Math.abs(activeDay[k]))}
                              </span>
                            </div>
                          ))
                      ) : (
                        <div className="flex items-center justify-between gap-3">
                          <span style={{ color: SERIES_COLOR[barSeries] }}>{SERIES_LABEL[barSeries]}</span>
                          <span className="money-num" style={{ color: SERIES_COLOR[barSeries] }}>
                            {barSeries === 'balance' && activeDay.balance < 0 ? '-' : ''}{formatCents(Math.abs(activeDay[barSeries]))}
                          </span>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>

              {/* 图例：line = 三系列显隐 toggle；bar = 单选系列 */}
              <div className="flex gap-2 mt-3">
                {(['expense', 'income', 'balance'] as SeriesKey[]).map(k => {
                  const on = trendMode === 'line' ? visible[k] : barSeries === k
                  return (
                    <button
                      key={k}
                      onClick={() => {
                        if (trendMode === 'line') setVisible(v => ({ ...v, [k]: !v[k] }))
                        else setBarSeries(k)
                        setActiveDay(null)
                      }}
                      className="flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium transition-colors"
                      style={{
                        background: on ? SERIES_COLOR[k] : 'var(--glass)',
                        color: on ? 'white' : 'var(--ink3)',
                      }}
                      aria-pressed={on}
                      data-testid={`trend-legend-${k}`}
                    >
                      <span className="w-2 h-2 rounded-full" style={{ background: on ? 'white' : SERIES_COLOR[k] }} />
                      {SERIES_LABEL[k]}
                    </button>
                  )
                })}
              </div>

              {/* 空态：全月零交易仍渲染零线 + 小字 */}
              {daily.every(d => d.expense === 0 && d.income === 0) && (
                <div className="text-center text-xs mt-2" style={{ color: 'var(--ink3)' }}>本期暂无记账</div>
              )}
            </div>

            {/* 预算执行 */}
            {budget && tab === 'expense' && (
              <div className="glass-card p-4">
                <h3 className="text-sm font-medium mb-2" style={{ color: 'var(--ink)' }}>{periodLabel(period)}预算</h3>
                <div className="flex justify-between text-xs text-ink3 mb-1">
                  <span>{formatCents(budgetSpentCents)}</span>
                  <span>{formatCents(budget.amountCents)}</span>
                </div>
                <div className="h-3 rounded-full overflow-hidden" style={{ background: 'var(--glass-border)' }}>
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${Math.min(100, Math.round(budgetSpentCents / budget.amountCents * 100))}%`,
                      background: budgetSpentCents > budget.amountCents ? 'var(--expense)' : 'var(--accent)',
                    }}
                  />
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
