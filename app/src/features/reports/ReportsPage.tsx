/**
 * ReportsPage — 报表页。
 *
 * 月度统计、分类占比环图、每日趋势、预算执行、环比。
 * 数据来源：Room + domain/aggregator.ts（纯本地）。
 */

import { useState, useEffect, useCallback } from 'react'
import { PageHeader } from '../../components/Layout'
import { EmptyState } from '../../components/StateViews'
import { PieChart, Pie, Cell, LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { useBridge } from '../../platform/hooks/use-bridge'
import { groupByCategory, sumTransactions } from '../../domain/aggregator'
import * as Money from '../../domain/money'
import type { TransactionData, CategoryData } from '../../platform/bridge/bridge-types'
import { toDomainTransaction } from '../../domain/types'
import { useChartColors } from '../../design/theme'
import { AiInsightCard } from './AiInsightCard'

function formatCents(cents: number): string {
  return Money.formatDisplay(Money.cents(cents))
}

export function ReportsPage() {
  const bridge = useBridge()
  const chartColors = useChartColors()
  const period = (() => {
    const now = new Date()
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
  })()
  const [bookId, setBookId] = useState<string | null>(null)
  const [txs, setTxs] = useState<TransactionData[]>([])
  const [categories, setCategories] = useState<CategoryData[]>([])
  const [budget, setBudget] = useState<{ amountCents: number } | null>(null)
  const [tab, setTab] = useState<'expense' | 'income'>('expense')

  const loadData = useCallback(async () => {
    try {
      const books = await bridge.call('book.list', {}) as { id: string }[]
      const id = books[0]?.id ?? null
      if (!id) return
      setBookId(id)

      const [allTxs, cats, budgets] = await Promise.all([
        bridge.call('transaction.listByBook', { bookId: id }) as Promise<TransactionData[]>,
        bridge.call('category.list', {}) as Promise<CategoryData[]>,
        // R-M4-30 修复：传递 bookId，与 Kotlin 侧 budget.list 契约一致
        bridge.call('budget.list', { bookId: id }) as Promise<{ amountCents: number; period: string; budgetType: string; categoryId: string | null }[]>,
      ])

      const monthTxs = allTxs.filter(tx => tx.bizDate.startsWith(period) && tx.deletedAt === null)
      setTxs(monthTxs)
      setCategories(cats)

      const monthBudget = budgets.find(b => b.period === period && b.budgetType === 'monthly' && !b.categoryId)
      setBudget(monthBudget ? { amountCents: monthBudget.amountCents } : null)
    } catch (err) {
      console.error('Failed to load reports:', err)
    }
  }, [bridge, period])

  useEffect(() => { loadData() }, [loadData])

  // 统计
  const stats = sumTransactions(txs.map(toDomainTransaction))

  // 分类占比
  const categoryData = groupByCategory(txs.map(toDomainTransaction), tab).map(b => {
    const cat = categories.find(c => c.id === b.categoryId)
    return {
      name: cat?.name || '未分类',
      value: b.amount as number,
      count: b.count,
    }
  }).sort((a, b) => b.value - a.value)

  // 每日趋势
  const dailyData = new Map<string, number>()
  for (const tx of txs.filter(t => t.type === tab)) {
    const day = tx.bizDate.slice(8) // DD
    dailyData.set(day, (dailyData.get(day) ?? 0) + tx.amountCents)
  }
  const trendData = Array.from(dailyData.entries())
    .map(([day, amount]) => ({ day: `${parseInt(day)}日`, amount }))
    .sort((a, b) => a.day.localeCompare(b.day))

  return (
    <div className="min-h-screen">
      <PageHeader title="报表" />

      <div className="px-4 py-4 space-y-4 pb-20">
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
        <AiInsightCard bridge={bridge} bookId={bookId} period={period} />

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

        {/* 每日趋势 */}
        {trendData.length > 0 && (
          <div className="glass-card p-4">
            <h3 className="text-sm font-medium mb-3" style={{ color: 'var(--ink)' }}>每日趋势</h3>
            <div className="h-32">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={trendData}>
                  <XAxis dataKey="day" tick={{ fontSize: 10, fill: 'var(--ink3)' }} axisLine={false} tickLine={false} />
                  <YAxis hide />
                  <Tooltip
                    formatter={(value) => formatCents(Number(value))}
                    contentStyle={{ background: 'var(--glass)', border: '1px solid var(--glass-border)', borderRadius: 8 }}
                  />
                  <Line
                    type="monotone"
                    dataKey="amount"
                    stroke={tab === 'expense' ? 'var(--expense)' : 'var(--income)'}
                    strokeWidth={2}
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}

        {/* 预算执行 */}
        {budget && tab === 'expense' && (
          <div className="glass-card p-4">
            <h3 className="text-sm font-medium mb-2" style={{ color: 'var(--ink)' }}>本月预算</h3>
            <div className="flex justify-between text-xs text-ink3 mb-1">
              <span>{formatCents(stats.totalExpense as number)}</span>
              <span>{formatCents(budget.amountCents)}</span>
            </div>
            <div className="h-3 rounded-full overflow-hidden" style={{ background: 'var(--glass-border)' }}>
              <div
                className="h-full rounded-full transition-all"
                style={{
                  width: `${Math.min(100, Math.round((stats.totalExpense as number) / budget.amountCents * 100))}%`,
                  background: (stats.totalExpense as number) > budget.amountCents ? 'var(--expense)' : 'var(--accent)',
                }}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
