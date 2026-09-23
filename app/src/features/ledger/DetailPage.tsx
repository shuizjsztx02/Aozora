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
 *
 * 第十二轮 REQ-33（T12-5）：搜索框缩为账本行右端圆钮，搜索能力迁至 /search 子页。
 *
 * 第十四轮 REQ-38（T14-1）：流水行换共享组件 TxListRow（本页为视觉基准，零变化）。
 */

import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router'
import { EmptyState } from '../../components/StateViews'
import { useBridge } from '../../platform/hooks/use-bridge'
import { useRefreshOnSync } from '../../platform/hooks/use-refresh-on-sync'
import { newUuid } from '../../platform/uuid'
import { pickEntryBook } from '../../domain/books'
import { CategoryIcon } from '../../components/CategoryIcon'
import { PeriodPanel } from '../../components/PeriodPanel'
import { TxListRow } from '../../components/TxListRow'
import { localHHmm } from '../../domain/date'
import { TxEditSheet } from './TxEditSheet'
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
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerYear, setPickerYear] = useState(() => new Date().getFullYear())
  const [books, setBooks] = useState<BookData[]>([])
  const [selectedBookId, setSelectedBookId] = useState<string | null>(null)
  const [bookPickerOpen, setBookPickerOpen] = useState(false)
  // T12-10（REQ-36）：点击流水行开编辑弹层
  const [editTx, setEditTx] = useState<TransactionData | null>(null)

  /**
   * 按所选账本加载流水（listByBook 不带期间；当前月过滤在渲染侧）。
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

  /**
   * @param silent T18-2（REQ-67①）：静默重取——同步完成 / 页面重新可见时刷新数据，
   *   但不置 loading（否则正在阅读的页面会闪一下加载态；挂载首读仍走非静默）。
   */
  const loadData = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const [bs, cats, meta] = await Promise.all([
        bridge.call('book.list', {}) as Promise<BookData[]>,
        bridge.call('category.list', {}) as Promise<CategoryData[]>,
        // T9-5b（REQ-11 决策 #3）：默认账本优先
        bridge.getMeta(['default_book_id']).catch(() => ({} as Record<string, string | null>)),
      ])
      setBooks(bs)
      setCategories(cats)
      // 默认选中 default_book_id 命中的非 master 账本；未设则第一个非 master（决策 #2/#3）
      const entry = pickEntryBook(bs, meta['default_book_id'] ?? null)
      setSelectedBookId(entry?.id ?? null)
      if (entry) {
        await loadTxs(entry.id, bs)
      } else {
        setAllTxs([])
      }
    } catch (err) {
      console.error('Failed to load detail:', err)
    } finally {
      if (!silent) setLoading(false)
    }
  }, [bridge, loadTxs])

  useEffect(() => {
    loadData()
  }, [loadData])

  // T18-2（REQ-67①）：同步完成 / 页面重新可见 → 静默重取
  const refreshQuiet = useCallback(() => { void loadData(true) }, [loadData])
  useRefreshOnSync(refreshQuiet)

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

  // ─── 按月筛选（搜索能力 T12-5/REQ-33 已迁出至 /search 子页） ───
  const visibleTxs = allTxs.filter(tx => tx.bizDate.startsWith(period))

  // 当月合计（右侧展示，随所选月份变化）
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
      {/* ─── 账本切换入口（REQ-4：替换原"明细"大标题位置；REQ-33：右端加搜索圆钮） ─── */}
      <div className="relative px-4 pt-2 pb-1">
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

          {/* T12-5（REQ-33）：搜索缩为圆钮（样式同期间行 ‹› 钮），点击进 /search 子页 */}
          <button
            data-testid="open-search"
            onClick={() => navigate('/search', { state: { bookId: selectedBookId } })}
            className="flex items-center justify-center w-8 h-8 rounded-full active:opacity-60"
            style={{ color: 'var(--ink)' }}
            aria-label="搜索"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <circle cx="11" cy="11" r="7" />
              <line x1="21" y1="21" x2="16.5" y2="16.5" />
            </svg>
          </button>
        </div>

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
                    <CategoryIcon icon={b.icon} name={b.name} fallback="📒" />
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

        {/* 年/月选择面板（T10-5：抽共享组件） */}
        <PeriodPanel
          open={pickerOpen}
          onClose={() => setPickerOpen(false)}
          year={pickerYear}
          onYearChange={setPickerYear}
          period={period}
          onPick={(p) => { setPeriod(p); setPickerOpen(false) }}
        />
      </div>

      {/* ─── 流水列表（搜索入口 T12-5 起为账本行右端圆钮 → /search 子页） ─── */}
      <div className="px-4">
        {loading ? (
          <div className="flex justify-center py-12">
            <div className="w-8 h-8 rounded-full border-2 border-t-transparent animate-spin" style={{ borderColor: 'var(--accent)', borderTopColor: 'transparent' }} />
          </div>
        ) : sortedDays.length === 0 ? (
          <EmptyState icon="📋" title="本月暂无记录" />
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
                    const sub = `${localHHmm(tx.occurredAt)} · ${cat?.name || '未分类'}`
                    return (
                      <TxListRow
                        key={tx.id}
                        icon={cat?.icon}
                        iconName={cat?.name ?? ''}
                        title={title}
                        sub={sub}
                        amountCents={tx.amountCents}
                        isExpense={tx.type === 'expense'}
                        aiBadge={tx.source === 'nlu_ai'}
                        borderBottom={i < dayTxs.length - 1}
                        onClick={() => setEditTx(tx)}
                        right={
                          <button
                            onClick={e => { e.stopPropagation(); handleDelete(tx.id) }}
                            className="shrink-0 text-xs p-1.5 active:opacity-60"
                            style={{ color: 'var(--ink3)' }}
                            aria-label="删除"
                          >
                            ✕
                          </button>
                        }
                      />
                    )
                  })}
                </div>
              </div>
            )
          })
        )}
      </div>

      {/* T12-10（REQ-36）：点行编辑弹层；保存/删除后按所选账本刷新列表 */}
      {editTx && (
        <TxEditSheet
          tx={editTx}
          categories={categories}
          onClose={() => setEditTx(null)}
          onSaved={() => {
            if (selectedBookId) loadTxs(selectedBookId, books).catch(err =>
              console.error('Failed to refresh after edit:', err))
          }}
        />
      )}
    </div>
  )
}
