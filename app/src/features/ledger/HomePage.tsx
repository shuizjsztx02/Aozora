/**
 * HomePage — 首页。
 *
 * 展示：今日支出、本月进度、近期流水、同步状态、快捷记账入口。
 */

import { useState, useEffect, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { PageHeader } from '../../components/Layout'
import { SyncBadge } from '../../components/SyncBadge'
import { EmptyState } from '../../components/StateViews'
import { useBridge } from '../../platform/hooks/use-bridge'
import { QuickEntrySheet } from './QuickEntrySheet'
import type { TransactionData, CategoryData } from '../../platform/bridge/bridge-types'
import * as Money from '../../domain/money'
import { todayBizDate } from '../../domain/date'
import { sumTransactions } from '../../domain/aggregator'
import { toDomainTransaction } from '../../domain/types'

function formatCents(cents: number): string {
  return Money.formatDisplay(Money.cents(cents))
}

export function HomePage() {
  const [showSheet, setShowSheet] = useState(false)
  const [todayTxs, setTodayTxs] = useState<TransactionData[]>([])
  const [monthTxs, setMonthTxs] = useState<TransactionData[]>([])
  const [categories, setCategories] = useState<CategoryData[]>([])
  const [budget, setBudget] = useState<{ amountCents: number } | null>(null)
  const bridge = useBridge()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  // R-M4-28 修复：深链 entry=1 时自动打开快速记账面板
  useEffect(() => {
    if (searchParams.get('entry') === '1') {
      setShowSheet(true)
      // 清除参数避免重复触发
      searchParams.delete('entry')
      setSearchParams(searchParams, { replace: true })
    }
  }, [searchParams, setSearchParams])

  const loadData = useCallback(async () => {
    try {
      const books = await bridge.call('book.list', {}) as { id: string }[]
      const bookId = books[0]?.id
      if (!bookId) return

      const today = todayBizDate('Asia/Shanghai')
      const month = today.slice(0, 7)

      const [allTxs, cats, budgets] = await Promise.all([
        bridge.call('transaction.listByBook', { bookId }) as Promise<TransactionData[]>,
        bridge.call('category.list', {}) as Promise<CategoryData[]>,
        // R-M4-30 修复：传递 bookId，与 Kotlin 侧 budget.list 契约一致
        bridge.call('budget.list', { bookId }) as Promise<{ amountCents: number; period: string }[]>,
      ])

      const todayExpenses = allTxs.filter(tx => tx.bizDate === today && tx.type === 'expense' && tx.deletedAt === null)
      const monthExpenses = allTxs.filter(tx => tx.bizDate.startsWith(month) && tx.type === 'expense' && tx.deletedAt === null)

      setTodayTxs(todayExpenses)
      setMonthTxs(monthExpenses)
      setCategories(cats)

      // 查找本月预算
      const monthBudget = budgets.find(b => b.period === month)
      setBudget(monthBudget ? { amountCents: monthBudget.amountCents } : null)
    } catch (err) {
      console.error('Failed to load home data:', err)
    }
  }, [bridge])

  useEffect(() => {
    loadData()
  }, [loadData])

  const todayExpense = sumTransactions(todayTxs.map(toDomainTransaction))

  const monthExpense = sumTransactions(monthTxs.map(toDomainTransaction))

  const budgetProgress = budget
    ? Math.min(100, Math.round((monthExpense.totalExpense as number) / budget.amountCents * 100))
    : 0

  return (
    <div className="min-h-screen">
      <PageHeader title="Aozora" right={<SyncBadge />} />

      <div className="px-4 py-4 space-y-4">
        {/* 今日支出卡片 */}
        <div className="glass-card p-5">
          <div className="text-sm text-ink2 mb-1">今日支出</div>
          <div className="money-num text-3xl text-expense">
            {formatCents(todayExpense.totalExpense as number)}
          </div>
          {todayExpense.count > 0 && (
            <div className="text-xs text-ink3 mt-1">{todayExpense.count} 笔</div>
          )}
        </div>

        {/* 本月预算进度 */}
        {budget && (
          <div className="glass-card p-4">
            <div className="flex justify-between items-center mb-2">
              <span className="text-sm text-ink2">本月预算</span>
              <span className="text-xs text-ink3">{budgetProgress}%</span>
            </div>
            <div className="h-2 rounded-full overflow-hidden" style={{ background: 'var(--glass-border)' }}>
              <div
                className="h-full rounded-full transition-all duration-300"
                style={{
                  width: `${budgetProgress}%`,
                  background: budgetProgress > 80 ? 'var(--expense)' : 'var(--accent)',
                }}
              />
            </div>
            <div className="flex justify-between text-xs text-ink3 mt-1">
              <span>{formatCents(monthExpense.totalExpense as number)}</span>
              <span>{formatCents(budget.amountCents)}</span>
            </div>
          </div>
        )}

        {/* 近期流水 */}
        <div>
          <div className="flex justify-between items-center mb-2 px-1">
            <h2 className="text-sm font-medium text-ink2">今日流水</h2>
            <button onClick={() => navigate('/detail')} className="text-xs text-accent">查看全部</button>
          </div>
          {todayTxs.length === 0 ? (
            <EmptyState icon="📝" title="今天还没有记录" description="点击下方按钮开始记账" />
          ) : (
            <div className="glass-card divide-y" style={{ borderColor: 'var(--glass-border)' }}>
              {todayTxs.slice(0, 10).map(tx => {
                const cat = categories.find(c => c.id === tx.categoryId)
                return (
                  <div key={tx.id} className="flex items-center justify-between px-4 py-3 row-line">
                    <div className="flex items-center gap-3">
                      <span className="text-lg">{cat ? getCategoryEmoji(cat.name) : '💰'}</span>
                      <div>
                        <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>
                          {cat?.name || '未分类'}
                          {tx.source === 'nlu_ai' && (
                            <span className="ai-badge ml-1.5 text-[10px] px-1 py-0.5 rounded" style={{ background: 'var(--glass)', color: 'var(--accent)', border: '1px solid var(--glass-border)' }}>AI</span>
                          )}
                        </div>
                        {tx.note && <div className="text-xs text-ink3 truncate max-w-[120px]">{tx.note}</div>}
                      </div>
                    </div>
                    <span className={`money-num text-sm ${tx.type === 'expense' ? 'text-expense' : 'text-income'}`}>
                      {tx.type === 'expense' ? '-' : '+'}{formatCents(tx.amountCents)}
                    </span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* AI 助手入口 */}
      <button
        onClick={() => navigate('/assistant')}
        className="fixed bottom-20 left-4 z-20 w-12 h-12 rounded-full shadow-lg flex items-center justify-center text-xl transition-transform active:scale-95"
        style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
        data-testid="assistant-btn"
        aria-label="AI 助手"
      >
        💬
      </button>

      {/* 快捷记账按钮 */}
      <button
        onClick={() => setShowSheet(true)}
        className="fixed bottom-20 right-4 z-20 w-14 h-14 rounded-full shadow-lg flex items-center justify-center text-2xl text-white transition-transform active:scale-95"
        style={{ background: 'var(--accent)' }}
        data-testid="quick-entry-btn"
        aria-label="快速记账"
      >
        ✏️
      </button>

      {/* 快速记账 Sheet */}
      {showSheet && (
        <QuickEntrySheet
          categories={categories}
          onClose={() => setShowSheet(false)}
          onSuccess={() => {
            setShowSheet(false)
            loadData()
          }}
        />
      )}
    </div>
  )
}

/** 根据分类名返回 emoji（简化实现） */
function getCategoryEmoji(name: string): string {
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
