/**
 * CalendarView — 报表页日历视图（第十一轮 REQ-25 / T11-3，决策 #6）。
 *
 * 纯展示组件，props `{txs, categories, period}`：
 * txs 须已按 period 过滤（ReportsPage 派生，调用方保证）——按 bizDate 的 DD 逐日分桶。
 * 7×6 定格网格（周一起，与 DateField 同款）；格 = 日号 + 当日净额，
 * 支出净额 `var(--expense)` 浅底 / 收入净额 `var(--income)` 浅底（色向看 tone，
 * 浅底为叠层 opacity）/ 无交易透明；今天 accent 描边（data-today）。
 * 下方 = 月度小结（复用 sumTransactions）+ 选中日流水（明细行简版）。
 * 零新增桥调用；期间/账本切换由 ReportsPage 的 state 驱动 props。
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import type { TransactionData, CategoryData } from '../../platform/bridge/bridge-types'
import { CategoryIcon } from '../../components/CategoryIcon'
import { sumTransactions } from '../../domain/aggregator'
import { toDomainTransaction } from '../../domain/types'
import * as Money from '../../domain/money'

const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日'] // 周一起（国内惯例，同 DateField）

interface CalendarViewProps {
  /** 已按 period 过滤的流水（调用方保证） */
  txs: TransactionData[]
  categories: CategoryData[]
  /** 'YYYY-MM' */
  period: string
}

function formatCents(cents: number): string {
  return Money.formatDisplay(Money.cents(cents))
}

/** 格内净额：负 "-¥x"、正 "+¥x"、收支相抵 "¥0.00" */
function formatNet(net: number): string {
  if (net > 0) return `+${formatCents(net)}`
  if (net < 0) return `-${formatCents(-net)}`
  return formatCents(0)
}

function fmtTime(ts: number): string {
  const d = new Date(ts)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** 日格着色档：支出净额 / 收入净额 / 收支相抵 / 无交易 */
type Tone = 'expense' | 'income' | 'zero' | 'none'

export function CalendarView({ txs, categories, period }: CalendarViewProps) {
  const [y, m] = period.split('-').map(Number)
  const daysInMonth = new Date(y, m, 0).getDate()
  const firstOffset = (new Date(y, m - 1, 1).getDay() + 6) % 7 // 周一起的首行空格数

  // 逐日分桶：day → 当日支出/收入分合计（txs 已 period 过滤 → 取 DD；越界防御跳过）
  const buckets = useMemo(() => {
    const map = new Map<number, { expense: number; income: number }>()
    for (const tx of txs) {
      const day = parseInt(tx.bizDate.slice(8), 10)
      if (!(day >= 1 && day <= daysInMonth)) continue
      const b = map.get(day) ?? { expense: 0, income: 0 }
      if (tx.type === 'expense') b.expense += tx.amountCents
      else b.income += tx.amountCents
      map.set(day, b)
    }
    return map
  }, [txs, daysInMonth])

  // 选中日：默认今天；今天不在该月（历史/未来月）则 1 日；切月重置
  const [selected, setSelected] = useState(() => {
    const now = new Date()
    return now.getFullYear() === y && now.getMonth() === m - 1 ? now.getDate() : 1
  })
  useEffect(() => {
    const [py, pm] = period.split('-').map(Number)
    const now = new Date()
    setSelected(now.getFullYear() === py && now.getMonth() === pm - 1 ? now.getDate() : 1)
  }, [period])

  const stats = useMemo(() => sumTransactions(txs.map(toDomainTransaction)), [txs])

  // 选中日流水（时间倒序，同明细页日内观感）
  const dayTxs = useMemo(() => {
    const ymd = `${period}-${String(selected).padStart(2, '0')}`
    return txs
      .filter(t => t.bizDate === ymd)
      .sort((a, b) => b.occurredAt - a.occurredAt)
  }, [txs, period, selected])

  // 6×7 = 42 格定格（切月不跳高）；跨月位置渲染透明占位
  const cells: ReactNode[] = []
  const today = new Date()
  const todayDay = today.getFullYear() === y && today.getMonth() === m - 1 ? today.getDate() : 0
  for (let i = 0; i < 42; i++) {
    const d = i - firstOffset + 1
    if (d < 1 || d > daysInMonth) {
      cells.push(<div key={`e${i}`} aria-hidden />)
      continue
    }
    const b = buckets.get(d)
    const net = b ? b.income - b.expense : 0
    const tone: Tone = !b ? 'none' : net < 0 ? 'expense' : net > 0 ? 'income' : 'zero'
    const isToday = d === todayDay
    const isSel = d === selected
    cells.push(
      <button
        key={d}
        onClick={() => setSelected(d)}
        data-testid={`cal-day-${d}`}
        data-tone={tone}
        data-today={isToday ? 'true' : undefined}
        aria-pressed={isSel}
        className="relative flex flex-col items-center justify-center overflow-hidden rounded-xl px-0.5 py-1.5 active:opacity-60"
        style={{
          color: tone === 'expense' ? 'var(--expense)' : tone === 'income' ? 'var(--income)' : 'var(--ink)',
          outline: isToday ? '1.5px solid var(--accent)' : undefined,
          boxShadow: isSel ? 'inset 0 0 0 1.5px var(--accent)' : undefined,
        }}
      >
        {tone !== 'none' && tone !== 'zero' && (
          <span
            aria-hidden
            data-tone-bg
            className="absolute inset-0"
            style={{ background: `var(--${tone})`, opacity: 0.13 }}
          />
        )}
        <span className="relative text-xs font-medium" style={{ color: 'var(--ink)' }}>{d}</span>
        {b && <span className="money-num relative text-[10px] leading-tight">{formatNet(net)}</span>}
      </button>,
    )
  }

  return (
    <div className="space-y-4" data-testid="calendar-view">
      {/* 月历 */}
      <div className="glass-card p-3">
        <div className="grid grid-cols-7 text-center text-[11px]" style={{ color: 'var(--ink3)' }}>
          {WEEKDAYS.map(w => <span key={w}>{w}</span>)}
        </div>
        <div className="mt-1 grid grid-cols-7 gap-1">
          {cells}
        </div>
        {stats.count === 0 && (
          <div className="mt-2 text-center text-xs" style={{ color: 'var(--ink3)' }}>本期暂无记账</div>
        )}
      </div>

      {/* 月度小结（复用 sumTransactions，三值样式同月度概览卡） */}
      <div className="glass-card p-4" data-testid="calendar-summary">
        <div className="text-sm text-ink2 mb-2">{period.replace('-', '年')}月小结</div>
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

      {/* 选中日流水（明细行简版：图标 + 分类名 + 符号金额 + 时间·备注副行） */}
      <div className="glass-card p-4">
        <h3 className="text-sm font-medium mb-2" style={{ color: 'var(--ink)' }}>{m}月{selected}日</h3>
        {dayTxs.length === 0 ? (
          <div className="py-4 text-center text-xs" style={{ color: 'var(--ink3)' }}>当日暂无交易</div>
        ) : (
          <div data-testid="calendar-day-list">
            {dayTxs.map((tx, i) => {
              const cat = categories.find(c => c.id === tx.categoryId)
              const sub = [fmtTime(tx.occurredAt), tx.note?.trim()].filter(Boolean).join(' · ')
              return (
                <div
                  key={tx.id}
                  className="flex items-center gap-3 py-2.5"
                  style={i < dayTxs.length - 1 ? { borderBottom: '1px solid var(--glass-border)' } : undefined}
                >
                  <div
                    className="flex items-center justify-center shrink-0 rounded-xl"
                    style={{ width: 40, height: 40, background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
                  >
                    <CategoryIcon icon={cat?.icon} name={cat?.name ?? ''} className="text-[19px] leading-none" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[15px] font-semibold truncate" style={{ color: 'var(--ink)' }}>
                      {cat?.name || '未分类'}
                    </div>
                    <div className="text-xs truncate mt-0.5" style={{ color: 'var(--ink3)' }}>{sub}</div>
                  </div>
                  <span className="money-num text-[15px] font-bold shrink-0" style={{ color: 'var(--ink)' }}>
                    {tx.type === 'expense' ? '-' : '+'}{formatCents(tx.amountCents)}
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
