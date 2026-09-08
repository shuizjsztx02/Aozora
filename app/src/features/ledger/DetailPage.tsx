/**
 * DetailPage — 明细页。
 *
 * 第六轮 REQ-1：按参考稿重排版——大标题、期间行（月份 + 当月支出/收入合计）、
 * 年/月切换面板、时间筛选下方搜索框（跨全部月份匹配分类/备注/金额/日期）。
 * 日分组流水行：圆角图标 + 名称加粗 + "HH:mm · 分类"副行 + 右对齐金额。
 * 删除/撤销不变（本轮补齐 mutationId，修复删除静默失败）。
 *
 * 第七轮 REQ-4：去掉"明细"大标题，原位置改为账本切换按钮（含总账本聚合视图——
 * master 不存流水，选中时对其余普通账本并行 listByBook 合并）。
 */

import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router'
import { EmptyState } from '../../components/StateViews'
import { useBridge } from '../../platform/hooks/use-bridge'
import { newUuid } from '../../platform/uuid'
import { pickEntryBook } from '../../domain/books'
import { getCategoryEmoji } from '../../components/category-emoji'
import type { BookData, TransactionData, CategoryData } from '../../platform/bridge/bridge-types'
import * as Money from '../../domain/money'

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

/** "55.00" — 参考稿金额无 ¥ 符号 */
function fmtAmount(cents: number): string {
  return Money.formatYuan(Money.cents(cents))
}

/** 合计金额：整元省略小数（"110"），非整元保留两位（"110.50"） */
function fmtTotal(cents: number): string {
  const s = fmtAmount(cents)
  return s.endsWith('.00') ? s.slice(0, -3) : s
}

function fmtTime(occurredAt: number): string {
  const d = new Date(occurredAt)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function dayLabel(bizDate: string): string {
  const [, m, d] = bizDate.split('-').map(Number)
  return `${m}月${d}日`
}

export function DetailPage() {
  const bridge = useBridge()
  const navigate = useNavigate()
  const [period, setPeriod] = useState(currentPeriod)
  const [allTxs, setAllTxs] = useState<TransactionData[]>([])
  const [categories, setCategories] = useState<CategoryData[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerYear, setPickerYear] = useState(() => new Date().getFullYear())
  const [books, setBooks] = useState<BookData[]>([])
  const [selectedBookId, setSelectedBookId] = useState<string | null>(null)
  const [bookPickerOpen, setBookPickerOpen] = useState(false)

  /**
   * 按所选账本加载全量流水（含所有月份，搜索需要跨月份匹配）。
   * 总账本自身不存流水（决策 #1）→ 对其余普通账本并行 listByBook 合并；
   * 过滤掉 selectedBookId 自身是重复保险。
   */
  const loadTxs = useCallback(async (bookId: string, bookList: BookData[]) => {
    const selected = bookList.find(b => b.id === bookId)
    const targets: { id: string }[] = selected?.isMaster
      ? bookList.filter(b => b.id !== bookId && !b.isMaster)
      : [{ id: bookId }]
    const lists = await Promise.all(
      targets.map(b => bridge.call('transaction.listByBook', { bookId: b.id }) as Promise<TransactionData[]>),
    )
    setAllTxs(lists.flat().filter(tx => tx.deletedAt === null))
  }, [bridge])

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [bs, cats] = await Promise.all([
        bridge.call('book.list', {}) as Promise<BookData[]>,
        bridge.call('category.list', {}) as Promise<CategoryData[]>,
      ])
      setBooks(bs)
      setCategories(cats)
      // 默认选中第一个非 master 账本（决策 #2，避开"总账本"排序坑）
      const entry = pickEntryBook(bs)
      setSelectedBookId(entry?.id ?? null)
      if (entry) {
        await loadTxs(entry.id, bs)
      } else {
        setAllTxs([])
      }
    } catch (err) {
      console.error('Failed to load detail:', err)
    } finally {
      setLoading(false)
    }
  }, [bridge, loadTxs])

  useEffect(() => {
    loadData()
  }, [loadData])

  const handleSelectBook = async (id: string) => {
    setBookPickerOpen(false)
    setSelectedBookId(id)
    setLoading(true)
    try {
      await loadTxs(id, books)
    } catch (err) {
      console.error('Failed to load book:', err)
    } finally {
      setLoading(false)
    }
  }

  const selectedBook = books.find(b => b.id === selectedBookId)

  const catOf = (tx: TransactionData) => categories.find(c => c.id === tx.categoryId)

  // ─── 搜索（跨全部月份）或按月筛选 ───
  const q = query.trim().toLowerCase()
  const searching = q.length > 0
  const visibleTxs = searching
    ? allTxs.filter(tx => {
        const cat = catOf(tx)
        const haystack = [
          cat?.name ?? '',
          tx.note ?? '',
          fmtAmount(tx.amountCents),
          String(tx.amountCents),
          tx.bizDate,               // 2026-09-03
          dayLabel(tx.bizDate),     // 9月3日
        ].join(' ').toLowerCase()
        return haystack.includes(q)
      })
    : allTxs.filter(tx => tx.bizDate.startsWith(period))

  // 当月合计（右侧展示，随所选月份变化；搜索时保持展示当月数字）
  const monthTxs = allTxs.filter(tx => tx.bizDate.startsWith(period))
  const monthExpense = monthTxs.filter(t => t.type === 'expense').reduce((s, t) => s + t.amountCents, 0)
  const monthIncome = monthTxs.filter(t => t.type === 'income').reduce((s, t) => s + t.amountCents, 0)

  // 按日分组
  const grouped = new Map<string, TransactionData[]>()
  for (const tx of visibleTxs) {
    const group = grouped.get(tx.bizDate) ?? []
    group.push(tx)
    grouped.set(tx.bizDate, group)
  }
  const sortedDays = Array.from(grouped.keys()).sort().reverse()

  const handleDelete = async (id: string) => {
    if (!window.confirm('确定要删除这条记录吗？')) return
    try {
      // 必须补 mutationId：native transaction.softDelete 期望 {id, mutationId}
      await bridge.softDeleteTransaction(id, newUuid())
      setAllTxs(prev => prev.filter(tx => tx.id !== id))
    } catch (err) {
      console.error('Failed to delete:', err)
    }
  }

  return (
    <div className="min-h-screen pb-20">
      {/* ─── 账本切换入口（REQ-4：替换原"明细"大标题位置） ─── */}
      <div className="relative px-4 pt-2 pb-1">
        <button
          onClick={() => setBookPickerOpen(v => !v)}
          className="flex items-center gap-1 text-[26px] font-bold active:opacity-60"
          style={{ color: 'var(--ink)' }}
          aria-label="切换账本"
          aria-expanded={bookPickerOpen}
        >
          {selectedBook && <span>{selectedBook.icon}</span>}
          <span>{selectedBook?.name ?? '选择账本'}</span>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.5 }}><polyline points="6 9 12 15 18 9" /></svg>
        </button>

        {/* 账本列表面板（复用年/月面板的 fixed 遮罩 + glass-card 样式） */}
        {bookPickerOpen && (
          <>
            <div className="fixed inset-0 z-10" onClick={() => setBookPickerOpen(false)} aria-hidden />
            <div
              className="glass-card absolute left-4 right-4 top-full z-20 mt-1 p-2"
              style={{ boxShadow: 'var(--card-shadow)' }}
            >
              {books.map(b => {
                const active = b.id === selectedBookId
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
                    <span>{b.icon}</span>
                    <span className="flex-1 truncate">{b.name}</span>
                    {b.isMaster && (
                      <span
                        className="rounded px-1.5 py-0.5 text-[11px]"
                        style={{
                          background: active ? 'rgba(255,255,255,0.25)' : 'var(--glass)',
                          color: active ? 'white' : 'var(--ink2)',
                        }}
                      >
                        汇总
                      </span>
                    )}
                  </button>
                )
              })}
              <button
                onClick={() => { setBookPickerOpen(false); navigate('/settings/books') }}
                className="mt-1 flex w-full items-center border-t px-3 py-2.5 text-left text-[14px]"
                style={{ color: 'var(--ink2)', borderColor: 'var(--glass-border)' }}
              >
                管理账本…
              </button>
            </div>
          </>
        )}
      </div>

      {/* ─── 期间行：‹ 2026年9月 › + 当月合计（点击年月打开选择面板） ─── */}
      <div className="relative px-4">
        <div
          className="flex items-center justify-between py-3"
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
          <div className="text-sm" style={{ color: 'var(--ink2)' }}>
            <span>支出 </span>
            <span className="money-num font-bold text-[15px]" style={{ color: 'var(--ink)' }}>{fmtTotal(monthExpense)}</span>
            {monthIncome > 0 && (
              <>
                <span className="ml-2">收入 </span>
                <span className="money-num font-bold text-[15px]" style={{ color: 'var(--ink)' }}>{fmtTotal(monthIncome)}</span>
              </>
            )}
          </div>
        </div>

        {/* 年/月选择面板 */}
        {pickerOpen && (
          <>
            <div className="fixed inset-0 z-10" onClick={() => setPickerOpen(false)} aria-hidden />
            <div
              className="glass-card absolute left-4 right-4 top-full z-20 mt-1 p-3"
              style={{ boxShadow: 'var(--card-shadow)' }}
            >
              <div className="flex items-center justify-between mb-2">
                <button
                  onClick={() => setPickerYear(y => y - 1)}
                  className="w-8 h-8 rounded-full flex items-center justify-center active:opacity-60"
                  style={{ color: 'var(--ink)' }}
                  aria-label="上一年"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
                </button>
                <span className="text-[15px] font-semibold" style={{ color: 'var(--ink)' }}>{pickerYear}年</span>
                <button
                  onClick={() => setPickerYear(y => y + 1)}
                  className="w-8 h-8 rounded-full flex items-center justify-center active:opacity-60"
                  style={{ color: 'var(--ink)' }}
                  aria-label="下一年"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
                </button>
              </div>
              <div className="grid grid-cols-4 gap-2">
                {Array.from({ length: 12 }, (_, i) => i + 1).map(m => {
                  const p = `${pickerYear}-${String(m).padStart(2, '0')}`
                  const active = p === period
                  return (
                    <button
                      key={m}
                      onClick={() => { setPeriod(p); setPickerOpen(false) }}
                      className="py-2 rounded-lg text-sm font-medium transition-colors"
                      style={{
                        background: active ? 'var(--accent)' : 'var(--glass)',
                        color: active ? 'white' : 'var(--ink2)',
                      }}
                    >
                      {m}月
                    </button>
                  )
                })}
              </div>
            </div>
          </>
        )}
      </div>

      {/* ─── 搜索框（匹配全部月份的账单卡片） ─── */}
      <div className="px-4 pt-3 pb-1">
        <div
          className="flex items-center gap-2 px-3 py-2 rounded-full"
          style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--ink3)" strokeWidth="2" strokeLinecap="round">
            <circle cx="11" cy="11" r="7" />
            <line x1="21" y1="21" x2="16.5" y2="16.5" />
          </svg>
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="搜索分类、备注、金额、日期…"
            className="flex-1 bg-transparent text-sm outline-none"
            style={{ color: 'var(--ink)' }}
            aria-label="搜索账单"
          />
          {query && (
            <button
              onClick={() => setQuery('')}
              className="w-5 h-5 rounded-full flex items-center justify-center text-xs"
              style={{ background: 'var(--glass-border)', color: 'var(--ink2)' }}
              aria-label="清空搜索"
            >
              ✕
            </button>
          )}
        </div>
        {searching && (
          <div className="px-1 pt-2 text-xs" style={{ color: 'var(--ink3)' }}>
            找到 {visibleTxs.length} 笔相关记录
          </div>
        )}
      </div>

      {/* ─── 流水列表 ─── */}
      <div className="px-4">
        {loading ? (
          <div className="flex justify-center py-12">
            <div className="w-8 h-8 rounded-full border-2 border-t-transparent animate-spin" style={{ borderColor: 'var(--accent)', borderTopColor: 'transparent' }} />
          </div>
        ) : sortedDays.length === 0 ? (
          searching
            ? <EmptyState icon="🔍" title="未找到相关记录" />
            : <EmptyState icon="📋" title="本月暂无记录" />
        ) : (
          sortedDays.map(day => {
            const dayTxs = grouped.get(day)!
              .slice()
              .sort((a, b) => b.occurredAt - a.occurredAt)
            const dayExpense = dayTxs.filter(t => t.type === 'expense').reduce((s, t) => s + t.amountCents, 0)
            const dayIncome = dayTxs.filter(t => t.type === 'income').reduce((s, t) => s + t.amountCents, 0)

            return (
              <div key={day} className="mb-3">
                {/* 日头：9月3日        支 -110.00 */}
                <div className="flex justify-between items-baseline py-2 px-1">
                  <span className="text-xs" style={{ color: 'var(--ink3)' }}>{dayLabel(day)}</span>
                  <span className="text-xs money-num" style={{ color: 'var(--ink2)' }}>
                    {dayExpense > 0 && <span>支 -{fmtAmount(dayExpense)}</span>}
                    {dayIncome > 0 && <span className="ml-2">收 +{fmtAmount(dayIncome)}</span>}
                  </span>
                </div>

                {/* 流水行 */}
                <div>
                  {dayTxs.map((tx, i) => {
                    const cat = catOf(tx)
                    const note = tx.note?.trim()
                    const title = note || cat?.name || '未分类'
                    const sub = `${fmtTime(tx.occurredAt)} · ${cat?.name || '未分类'}`
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
                          <span className="text-[19px] leading-none">{cat ? getCategoryEmoji(cat.name) : '💰'}</span>
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="text-[15px] font-semibold truncate" style={{ color: 'var(--ink)' }}>
                            {title}
                            {tx.source === 'nlu_ai' && (
                              <span
                                className="ml-1.5 align-middle text-[9px] px-1 py-px rounded"
                                style={{ background: 'var(--glass)', color: 'var(--accent)', border: '1px solid var(--glass-border)' }}
                              >
                                AI
                              </span>
                            )}
                          </div>
                          <div className="text-xs truncate mt-0.5" style={{ color: 'var(--ink3)' }}>{sub}</div>
                        </div>
                        <span className="money-num text-[17px] font-bold shrink-0" style={{ color: 'var(--ink)' }}>
                          {tx.type === 'expense' ? '-' : '+'}{fmtAmount(tx.amountCents)}
                        </span>
                        <button
                          onClick={() => handleDelete(tx.id)}
                          className="shrink-0 text-xs p-1.5 active:opacity-60"
                          style={{ color: 'var(--ink3)' }}
                          aria-label="删除"
                        >
                          ✕
                        </button>
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
