/**
 * DetailPage — 明细页。
 *
 * 月份切换、日分组、筛选、详情、删除/撤销。
 */

import { useState, useEffect, useCallback } from 'react'
import { PageHeader } from '../../components/Layout'
import { EmptyState } from '../../components/StateViews'
import { useBridge } from '../../platform/hooks/use-bridge'
import type { TransactionData, CategoryData } from '../../platform/bridge/bridge-types'
import * as Money from '../../domain/money'

type Filter = 'all' | 'expense' | 'income'

function formatCents(cents: number): string {
  return Money.formatDisplay(Money.cents(cents))
}

function getMonthLabel(period: string): string {
  const [y, m] = period.split('-')
  return `${y}年${parseInt(m)}月`
}

function prevMonth(period: string): string {
  const [y, m] = period.split('-').map(Number)
  const d = new Date(y, m - 2, 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

function nextMonth(period: string): string {
  const [y, m] = period.split('-').map(Number)
  const d = new Date(y, m, 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

export function DetailPage() {
  const bridge = useBridge()
  const [period, setPeriod] = useState(() => {
    const now = new Date()
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
  })
  const [filter, setFilter] = useState<Filter>('all')
  const [txs, setTxs] = useState<TransactionData[]>([])
  const [categories, setCategories] = useState<CategoryData[]>([])
  const [loading, setLoading] = useState(true)

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const books = await bridge.call('book.list', {}) as { id: string }[]
      const bookId = books[0]?.id
      if (!bookId) { setLoading(false); return }

      const [allTxs, cats] = await Promise.all([
        bridge.call('transaction.listByBook', { bookId }) as Promise<TransactionData[]>,
        bridge.call('category.list', {}) as Promise<CategoryData[]>,
      ])

      // 按月份过滤
      const filtered = allTxs.filter(tx => tx.bizDate.startsWith(period) && tx.deletedAt === null)
      setTxs(filtered)
      setCategories(cats)
    } catch (err) {
      console.error('Failed to load detail:', err)
    } finally {
      setLoading(false)
    }
  }, [bridge, period])

  useEffect(() => {
    loadData()
  }, [loadData])

  // 筛选
  const filteredTxs = filter === 'all' ? txs : txs.filter(tx => tx.type === filter)

  // 按日分组
  const grouped = new Map<string, TransactionData[]>()
  for (const tx of filteredTxs) {
    const group = grouped.get(tx.bizDate) ?? []
    group.push(tx)
    grouped.set(tx.bizDate, group)
  }

  const sortedDays = Array.from(grouped.keys()).sort().reverse()

  const handleDelete = async (id: string) => {
    if (!window.confirm('确定要删除这条记录吗？')) return
    try {
      await bridge.call('transaction.softDelete', { id })
      setTxs(prev => prev.filter(tx => tx.id !== id))
    } catch (err) {
      console.error('Failed to delete:', err)
    }
  }

  return (
    <div className="min-h-screen">
      <PageHeader title="明细" />

      {/* 月份切换 */}
      <div className="flex items-center justify-between px-4 py-3">
        <button onClick={() => setPeriod(prevMonth(period))} className="px-3 py-1 rounded-lg key-btn text-sm">
          ←
        </button>
        <span className="serif-date text-sm" style={{ color: 'var(--ink)' }}>
          {getMonthLabel(period)}
        </span>
        <button onClick={() => setPeriod(nextMonth(period))} className="px-3 py-1 rounded-lg key-btn text-sm">
          →
        </button>
      </div>

      {/* 筛选 */}
      <div className="flex gap-2 px-4 mb-3">
        {(['all', 'expense', 'income'] as Filter[]).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className="px-3 py-1 rounded-full text-xs font-medium transition-colors"
            style={{
              background: filter === f ? 'var(--accent)' : 'var(--glass)',
              color: filter === f ? 'white' : 'var(--ink2)',
            }}
          >
            {f === 'all' ? '全部' : f === 'expense' ? '支出' : '收入'}
          </button>
        ))}
      </div>

      {/* 流水列表 */}
      <div className="px-4 pb-20">
        {loading ? (
          <div className="flex justify-center py-12">
            <div className="w-8 h-8 rounded-full border-2 border-t-transparent animate-spin" style={{ borderColor: 'var(--accent)', borderTopColor: 'transparent' }} />
          </div>
        ) : sortedDays.length === 0 ? (
          <EmptyState icon="📋" title="本月暂无记录" />
        ) : (
          sortedDays.map(day => {
            const dayTxs = grouped.get(day)!
            const dayExpense = dayTxs.filter(t => t.type === 'expense').reduce((s, t) => s + t.amountCents, 0)
            const dayIncome = dayTxs.filter(t => t.type === 'income').reduce((s, t) => s + t.amountCents, 0)

            return (
              <div key={day} className="mb-4">
                {/* 日头 */}
                <div className="flex justify-between items-center py-2 px-1">
                  <span className="serif-date text-xs" style={{ color: 'var(--ink2)' }}>
                    {day.slice(5).replace('-', '月')}日
                  </span>
                  <span className="text-xs text-ink3">
                    {dayExpense > 0 && <span className="text-expense">支 {formatCents(dayExpense)}</span>}
                    {dayIncome > 0 && <span className="text-income ml-2">收 {formatCents(dayIncome)}</span>}
                  </span>
                </div>

                {/* 流水行 */}
                <div className="glass-card overflow-hidden">
                  {dayTxs.map((tx, i) => {
                    const cat = categories.find(c => c.id === tx.categoryId)
                    return (
                      <div key={tx.id} className={`flex items-center justify-between px-4 py-3 ${i < dayTxs.length - 1 ? 'row-line' : ''}`}>
                        <div className="flex items-center gap-3 flex-1 min-w-0">
                          <span className="text-lg">{cat ? getEmoji(cat.name) : '💰'}</span>
                          <div className="min-w-0">
                            <div className="text-sm font-medium truncate" style={{ color: 'var(--ink)' }}>
                              {cat?.name || '未分类'}
                            </div>
                            {tx.note && <div className="text-xs text-ink3 truncate">{tx.note}</div>}
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className={`money-num text-sm ${tx.type === 'expense' ? 'text-expense' : 'text-income'}`}>
                            {tx.type === 'expense' ? '-' : '+'}{formatCents(tx.amountCents)}
                          </span>
                          <button
                            onClick={() => handleDelete(tx.id)}
                            className="text-xs text-ink3 hover:text-expense transition-colors p-1"
                            aria-label="删除"
                          >
                            ✕
                          </button>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}

function getEmoji(name: string): string {
  const map: Record<string, string> = {
    '餐饮': '🍽️', '早餐': '🥐', '午餐': '🍱', '晚餐': '🍜', '零食': '🍪',
    '交通': '🚇', '打车': '🚕', '公交': '🚌',
    '购物': '🛍️', '日用': '🧴', '服饰': '👔',
    '娱乐': '🎮', '电影': '🎬', '游戏': '🎲',
    '居住': '🏠', '房租': '🏢', '水电': '💡',
    '医疗': '🏥', '通讯': '📱', '学习': '📚',
    '工资': '💰', '奖金': '🎁', '理财': '📈',
  }
  return map[name] || '💰'
}
