/**
 * BudgetPage — 预算管理页。
 *
 * 列表展示 + 新增月度/年度预算 + 删除。
 * T11-1（REQ-23 / 决策 #3）：账本切换（过滤 master）+ 期间筛选（PeriodPanel）
 * + 行点击编辑 + 页顶「预算沿用」开关（meta 写 budget_carryover，复制逻辑在
 * Repository.ensureBudgetCarryover 冷启动兑现，前端不重复实现）。
 */

import { useState, useEffect, useCallback } from 'react'
import { PageHeader } from '../../components/Layout'
import { CategoryIcon } from '../../components/CategoryIcon'
import { PeriodPanel } from '../../components/PeriodPanel'
import { useBridge } from '../../platform/hooks/use-bridge'
import { newUuid } from '../../platform/uuid'
import type { BookData, BudgetData, CategoryData } from '../../platform/bridge/bridge-types'
import { pickEntryBook } from '../../domain/books'
import * as Money from '../../domain/money'

function formatCents(cents: number): string {
  return Money.formatDisplay(Money.cents(cents))
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

export function BudgetPage() {
  const bridge = useBridge()
  const [books, setBooks] = useState<BookData[]>([])
  const [selectedBookId, setSelectedBookId] = useState<string | null>(null)
  const [bookPickerOpen, setBookPickerOpen] = useState(false)
  const [period, setPeriod] = useState(currentPeriod)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerYear, setPickerYear] = useState(() => new Date().getFullYear())
  const [budgets, setBudgets] = useState<BudgetData[]>([])
  const [categories, setCategories] = useState<CategoryData[]>([])
  const [loading, setLoading] = useState(true)
  const [carryover, setCarryover] = useState(false)
  const [switchError, setSwitchError] = useState<string | null>(null)
  // 表单两态：add = 新建，editing = 被编辑的既有预算（决策 #3 行点击进编辑态）
  const [showAdd, setShowAdd] = useState(false)
  const [editing, setEditing] = useState<BudgetData | null>(null)
  const [amount, setAmount] = useState('')
  const [budgetType, setBudgetType] = useState<'monthly' | 'annual'>('monthly')
  const [selectedCategoryId, setSelectedCategoryId] = useState<string>('')
  const [error, setError] = useState<string | null>(null)

  const formOpen = showAdd || editing !== null

  // 挂载：账本（含默认 id）+ 分类 + 沿用开关。预算列表按 selectedBookId 二次加载。
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [bookList, meta, cats] = await Promise.all([
          bridge.call('book.list', {}) as Promise<BookData[]>,
          bridge.getMeta(['default_book_id', 'budget_carryover']).catch(() => ({}) as Record<string, string | null>),
          bridge.call('category.list', {}) as Promise<CategoryData[]>,
        ])
        if (cancelled) return
        setBooks(bookList)
        setCarryover(meta['budget_carryover'] === '1')
        setCategories(cats.filter(c => c.kind === 'expense'))
        // 决策 #3 / T9-5b（决策 #3）：预算只挂普通账本；default_book_id 优先
        setSelectedBookId(pickEntryBook(bookList, meta['default_book_id'] ?? null)?.id ?? null)
      } catch (err) {
        console.error('Failed to load budget page data:', err)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [bridge])

  const reloadBudgets = useCallback(async (bookId: string) => {
    const list = await bridge.call('budget.list', { bookId }) as BudgetData[]
    setBudgets(list)
  }, [bridge])

  useEffect(() => {
    if (!selectedBookId) {
      setBudgets([])
      return
    }
    reloadBudgets(selectedBookId).catch(err => console.error('Failed to load budgets:', err))
  }, [selectedBookId, reloadBudgets])

  const closeForm = () => {
    setShowAdd(false)
    setEditing(null)
    setAmount('')
    setBudgetType('monthly')
    setSelectedCategoryId('')
    setError(null)
  }

  const openEdit = (bud: BudgetData) => {
    setShowAdd(false)
    setEditing(bud)
    setBudgetType(bud.budgetType)
    setAmount((bud.amountCents / 100).toFixed(2))
    setSelectedCategoryId(bud.categoryId ?? '')
    setError(null)
  }

  // 决策 #3：表单 period 固定取当前查看期间（月预算 'YYYY-MM' / 年预算 'YYYY'），
  // 不再取设备当月（修复原硬伤）。
  const viewPeriod = budgetType === 'monthly' ? period : period.slice(0, 4)

  const handleSubmit = async () => {
    const amountNum = parseFloat(amount)
    if (isNaN(amountNum) || amountNum <= 0) return
    if (!selectedBookId) {
      setError('请先创建账本，再添加预算')
      return
    }
    setError(null)
    try {
      if (editing) {
        // 编辑走 updateBudget（version+1；分类/类型/金额可改，period 锚定查看期间）
        await bridge.updateBudget(
          {
            id: editing.id,
            amountCents: Math.round(amountNum * 100),
            budgetType,
            categoryId: selectedCategoryId || null,
            period: viewPeriod,
            version: editing.version + 1,
          },
          newUuid()
        )
      } else {
        // 必须走 insertBudget 封装：native 侧期望 {budget, mutationId} 包裹结构
        await bridge.insertBudget(
          {
            id: newUuid(), // 服务端 sync/ai 契约要求 UUID（第六轮 Bug-5.2）
            bookId: selectedBookId,
            budgetType,
            categoryId: selectedCategoryId || null,
            period: viewPeriod,
            amountCents: Math.round(amountNum * 100),
            version: 1,
          },
          newUuid()
        )
      }
      await reloadBudgets(selectedBookId)
      closeForm()
    } catch (err) {
      console.error('Failed to save budget:', err)
      setError('保存预算失败，请重试。若多次失败请通过意见反馈报告问题')
      // 失败时表单与输入保留（第四轮 Bug-3 约定）
    }
  }

  const handleDelete = async (id: string) => {
    if (!window.confirm('确定要删除这条预算吗？')) return
    setError(null)
    try {
      // deleteBudget 封装会补齐 mutationId（同步队列需要，必须为 UUID）
      await bridge.deleteBudget(id, newUuid())
      if (selectedBookId) await reloadBudgets(selectedBookId)
    } catch (err) {
      console.error('Failed to delete budget:', err)
      setError('删除预算失败，请重试')
    }
  }

  const toggleCarryover = async () => {
    const next = !carryover
    setSwitchError(null)
    try {
      await bridge.setMeta('budget_carryover', next ? '1' : '0')
      setCarryover(next)
    } catch (err) {
      console.error('Failed to set budget_carryover:', err)
      setSwitchError('设置失败，请重试')
    }
  }

  const selectableBooks = books.filter(b => !b.isMaster)
  const selectedBook = books.find(b => b.id === selectedBookId)
  // 决策 #3：列表 = 该账本 查看月 monthly + 查看年 annual
  const visible = budgets.filter(
    b => (b.budgetType === 'monthly' && b.period === period)
      || (b.budgetType === 'annual' && b.period === period.slice(0, 4))
  )
  const expenseCats = categories

  return (
    <div className="min-h-screen">
      <PageHeader back />

      {/* ─── 账本切换（样式对齐 ReportsPage，决策 #3 过滤 master） ─── */}
      <div className="relative px-4 pt-1">
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

        {bookPickerOpen && (
          <>
            <div className="fixed inset-0 z-10" onClick={() => setBookPickerOpen(false)} aria-hidden />
            <div
              className="glass-card absolute left-4 right-4 top-full z-20 mt-1 p-2"
              style={{ boxShadow: 'var(--card-shadow)' }}
            >
              {selectableBooks.map(b => {
                const active = b.id === selectedBookId
                return (
                  <button
                    key={b.id}
                    onClick={() => { setSelectedBookId(b.id); setBookPickerOpen(false); closeForm() }}
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

      {/* ─── 期间行：‹ 2026年9月 › + PeriodPanel（复用共享组件） ─── */}
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
        {/* ─── 预算沿用开关（决策 #2：复制在冷启动兑现，此处仅写开关） ─── */}
        <div className="glass-card px-4 py-3 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>预算沿用</div>
            <div className="text-xs text-ink3">开启后每月自动沿用上月的月度预算（重启 App 后生效）</div>
          </div>
          <button
            role="switch"
            aria-checked={carryover}
            data-testid="budget-carryover-switch"
            onClick={toggleCarryover}
            className="relative w-11 h-6 rounded-full transition-colors shrink-0"
            style={{ background: carryover ? 'var(--accent)' : 'var(--glass)', border: '1px solid var(--glass-border)' }}
          >
            <span
              className="absolute top-[2px] w-5 h-5 rounded-full transition-all"
              style={{ left: carryover ? '22px' : '2px', background: 'white' }}
            />
          </button>
        </div>
        {switchError && (
          <div className="text-xs" style={{ color: 'var(--expense)' }}>{switchError}</div>
        )}

        {/* 预算列表（行点击进编辑态） */}
        <div className="glass-card overflow-hidden">
          {visible.length === 0 && !loading ? (
            <div className="px-4 py-8 text-center text-sm text-ink3">暂无预算，点击下方添加</div>
          ) : visible.length > 0 ? (
            visible.map((bud, i) => (
              <div
                key={bud.id}
                onClick={() => openEdit(bud)}
                className={`flex items-center justify-between px-4 py-3 active:opacity-60 ${i < visible.length - 1 ? 'row-line' : ''}`}
              >
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
                    onClick={e => { e.stopPropagation(); handleDelete(bud.id) }}
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
        {!formOpen && (
          <button
            onClick={() => { setShowAdd(true); setError(null) }}
            className="w-full py-3 rounded-lg text-sm font-medium transition-colors"
            style={{ background: 'var(--accent)', color: 'white' }}
          >
            ＋ 添加预算
          </button>
        )}

        {/* 新建 / 编辑表单（同一控件集，editing 决定提交路径） */}
        {formOpen && (
          <div className="glass-card p-4 space-y-3">
            <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>
              {editing ? '编辑预算' : '新建预算'}
            </div>

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

            {/* 期间提示（决策 #3：固定为当前查看期间） */}
            <div className="text-xs text-ink3">
              期间：{budgetType === 'monthly' ? periodLabel(period) : `${period.slice(0, 4)}年`}
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
                onClick={closeForm}
                className="flex-1 py-2 rounded-lg text-sm"
                style={{ background: 'var(--glass)', color: 'var(--ink2)' }}
              >
                取消
              </button>
              <button
                onClick={handleSubmit}
                disabled={!amount || parseFloat(amount) <= 0}
                className="flex-1 py-2 rounded-lg text-sm font-medium text-white transition-opacity disabled:opacity-40"
                style={{ background: 'var(--accent)' }}
              >
                {editing ? '保存' : '添加'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
