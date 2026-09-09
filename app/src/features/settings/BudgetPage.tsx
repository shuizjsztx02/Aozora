/**
 * BudgetPage — 预算管理页。
 *
 * 列表展示 + 新增月度/年度预算。
 */

import { useState, useEffect, useCallback } from 'react'
import { PageHeader } from '../../components/Layout'
import { CategoryIcon } from '../../components/CategoryIcon'
import { useBridge } from '../../platform/hooks/use-bridge'
import { newUuid } from '../../platform/uuid'
import type { BudgetData, CategoryData } from '../../platform/bridge/bridge-types'
import { pickEntryBook } from '../../domain/books'
import * as Money from '../../domain/money'

function formatCents(cents: number): string {
  return Money.formatDisplay(Money.cents(cents))
}

export function BudgetPage() {
  const bridge = useBridge()
  const [budgets, setBudgets] = useState<BudgetData[]>([])
  const [categories, setCategories] = useState<CategoryData[]>([])
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(false)
  const [amount, setAmount] = useState('')
  const [budgetType, setBudgetType] = useState<'monthly' | 'annual'>('monthly')
  const [selectedCategoryId, setSelectedCategoryId] = useState<string>('')
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      // budget.list 需要 bookId（Kotlin 侧为必填），先取 book 再并行查预算与分类
      // 决策 #2 / T7-6：预算只挂普通账本；T9-5b（决策 #3）default_book_id 优先
      const [books, meta] = await Promise.all([
        bridge.call('book.list', {}) as Promise<{ id: string; isMaster?: boolean }[]>,
        bridge.getMeta(['default_book_id']).catch(() => ({}) as Record<string, string | null>),
      ])
      const bookId = pickEntryBook(books, meta['default_book_id'] ?? null)?.id
      if (bookId) {
        ;(window as any).__aozoraBookId = bookId
      }
      const [budgetList, cats] = await Promise.all([
        bookId
          ? bridge.call('budget.list', { bookId }) as Promise<BudgetData[]>
          : Promise.resolve([] as BudgetData[]),
        bridge.call('category.list', {}) as Promise<CategoryData[]>,
      ])
      setBudgets(budgetList)
      setCategories(cats.filter(c => c.kind === 'expense'))
    } catch (err) {
      console.error('Failed to load budgets:', err)
    } finally {
      setLoading(false)
    }
  }, [bridge])

  useEffect(() => { load() }, [load])

  const handleAdd = async () => {
    const amountNum = parseFloat(amount)
    if (isNaN(amountNum) || amountNum <= 0) return
    setError(null)
    const bookId = (window as any).__aozoraBookId
    if (!bookId) {
      setError('请先创建账本，再添加预算')
      return
    }

    const now = new Date()
    const period = budgetType === 'monthly'
      ? `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
      : `${now.getFullYear()}`

    // 服务端 sync/ai 契约要求 UUID（第六轮 Bug-5.2）
    const id = newUuid()
    try {
      // 必须走 insertBudget 封装：native 侧期望 {budget, mutationId} 包裹结构
      await bridge.insertBudget(
        {
          id,
          bookId,
          budgetType,
          categoryId: selectedCategoryId || null,
          period,
          amountCents: Math.round(amountNum * 100),
          version: 1,
        },
        newUuid()
      )
      setAmount('')
      setSelectedCategoryId('')
      setShowAdd(false)
      await load()
    } catch (err) {
      console.error('Failed to add budget:', err)
      setError('创建预算失败，请重试。若多次失败请通过意见反馈报告问题')
    }
  }

  const handleDelete = async (id: string) => {
    if (!window.confirm('确定要删除这条预算吗？')) return
    setError(null)
    try {
      // deleteBudget 封装会补齐 mutationId（同步队列需要，必须为 UUID）
      await bridge.deleteBudget(id, newUuid())
      await load()
    } catch (err) {
      console.error('Failed to delete budget:', err)
      setError('删除预算失败，请重试')
    }
  }

  const expenseCats = categories

  return (
    <div className="min-h-screen">
      <PageHeader back />

      <div className="px-4 py-4 space-y-4 pb-20">
        {/* 预算列表 */}
        <div className="glass-card overflow-hidden">
          {budgets.length === 0 && !loading ? (
            <div className="px-4 py-8 text-center text-sm text-ink3">暂无预算，点击下方添加</div>
          ) : budgets.length > 0 ? (
            budgets.map((bud, i) => (
              <div key={bud.id} className={`flex items-center justify-between px-4 py-3 ${i < budgets.length - 1 ? 'row-line' : ''}`}>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>
                    {bud.budgetType === 'monthly' ? '月度' : '年度'}预算
                    {bud.categoryId && <span className="text-xs text-ink2 ml-1">
                      · {expenseCats.find(c => c.id === bud.categoryId)?.name || '分类'}
                    </span>}
                  </div>
                  <div className="text-xs text-ink3">{bud.period}</div>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium" style={{ color: 'var(--accent)' }}>
                    {formatCents(bud.amountCents)}
                  </span>
                  <button
                    onClick={() => handleDelete(bud.id)}
                    className="text-xs text-ink3 hover:text-expense transition-colors p-1"
                    aria-label="删除"
                  >

                  </button>
                </div>
              </div>
            ))
          ) : (
            <div className="px-4 py-3 text-xs text-ink3">加载中…</div>
          )}
        </div>

        {/* 添加按钮 */}
        {!showAdd && (
          <button
            onClick={() => setShowAdd(true)}
            className="w-full py-3 rounded-lg text-sm font-medium transition-colors"
            style={{ background: 'var(--accent)', color: 'white' }}
          >
            ＋ 添加预算
          </button>
        )}

        {/* 添加表单 */}
        {showAdd && (
          <div className="glass-card p-4 space-y-3">
            <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>新建预算</div>

            {/* 预算类型 */}
            <div className="flex gap-2">
              {(['monthly', 'annual'] as const).map(t => (
                <button
                  key={t}
                  onClick={() => setBudgetType(t)}
                  className="flex-1 py-2 rounded-lg text-sm font-medium transition-colors"
                  style={{
                    background: budgetType === t ? 'var(--accent)' : 'var(--glass)',
                    color: budgetType === t ? 'white' : 'var(--ink2)',
                  }}
                >
                  {t === 'monthly' ? '月度' : '年度'}
                </button>
              ))}
            </div>

            {/* 金额 */}
            <div>
              <div className="text-xs text-ink2 mb-1">预算金额 (¥)</div>
              <input
                type="number"
                value={amount}
                onChange={e => setAmount(e.target.value)}
                placeholder="0.00"
                className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
                autoFocus
              />
            </div>

            {/* 分类 (可选) — UI-16：内联宫格替代原生 <select>，首格"总预算" */}
            <div>
              <div className="text-xs text-ink2 mb-1">分类 (可选，不设则为总预算)</div>
              <div className="grid grid-cols-5 gap-2" data-testid="budget-category-grid">
                <button
                  onClick={() => setSelectedCategoryId('')}
                  className={`cat-cell text-xs ${selectedCategoryId === '' ? 'active' : ''}`}
                  style={{
                    background: selectedCategoryId === '' ? 'var(--glass)' : 'transparent',
                  }}
                >
                  <span className="text-lg">💰</span>
                  <span className="truncate max-w-full text-ink2">总预算</span>
                </button>
                {expenseCats.map(cat => (
                  <button
                    key={cat.id}
                    onClick={() => setSelectedCategoryId(cat.id)}
                    className={`cat-cell text-xs ${selectedCategoryId === cat.id ? 'active' : ''}`}
                    style={{
                      background: selectedCategoryId === cat.id ? 'var(--glass)' : 'transparent',
                    }}
                  >
                    <CategoryIcon icon={cat.icon} name={cat.name} className="text-lg" />
                    <span className="truncate max-w-full text-ink2">{cat.name}</span>
                  </button>
                ))}
              </div>
            </div>

            {error && (
              <div className="text-xs" style={{ color: 'var(--expense)' }}>{error}</div>
            )}
            <div className="flex gap-2">
              <button
                onClick={() => { setShowAdd(false); setAmount(''); setSelectedCategoryId(''); setError(null) }}
                className="flex-1 py-2 rounded-lg text-sm"
                style={{ background: 'var(--glass)', color: 'var(--ink2)' }}
              >
                取消
              </button>
              <button
                onClick={handleAdd}
                disabled={!amount || parseFloat(amount) <= 0}
                className="flex-1 py-2 rounded-lg text-sm font-medium text-white transition-opacity disabled:opacity-40"
                style={{ background: 'var(--accent)' }}
              >
                添加
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
