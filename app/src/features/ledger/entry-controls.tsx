/**
 * entry-controls — 共享记账控件（T12-6 / REQ-35 上半）。
 *
 * 自 QuickEntrySheet 原样抽出，交互逐字不变，供手动记账弹框 / 聊天卡编辑态
 * （T12-9）/ 交易编辑层（T12-10）共用：
 * - CategoryPager：分类宫格，每页 10 格（2×5）等高，>10 类 touch 横滑翻页 + 指示点，
 *   末页补隐形占位格（T9-1/UI-18）。页码受控于宿主（切收支时归零等策略由宿主定）。
 *   T19-5（REQ-76①）：传入的仍是**扁平全量**（过滤口径不动），组件自行按 parentId 分组——
 *   一级进宫格、二级进「选中/展开的一级」下方的内嵌面板，一二级的阶层关系可见。
 * - EntryChipRow：账本 / 账户 / 预算三 chip + 单开下拉（openPicker 态组件内持有）。
 */

import { useCallback, useMemo, useRef, useState } from 'react'
import { CategoryIcon } from '../../components/CategoryIcon'
import { pickEntryBook } from '../../domain/books'
import type { AccountData, BookData, CategoryData } from '../../platform/bridge/bridge-types'

// ─── CategoryPager ─────────────────────────────────────────────────────

export interface CategoryPagerProps {
  /** 已按收支等口径过滤好的分类列表（过滤策略由宿主决定）。**扁平全量、含一二级**——
   *  层级由组件内按 parentId 自行分组（T19-5），宿主不必预先拆。 */
  categories: CategoryData[]
  selectedId: string
  onSelect: (id: string) => void
  /** 当前页码（受控；越界由组件内 clamp，与抽取前 safeCatPage 行为一致）。 */
  page: number
  onPageChange: (page: number) => void
}

/** QuickEntrySheet（REQ-3 / 决策 #6）原 :706-767 段，标记与阈值逐字搬移。 */
export function CategoryPager({ categories, selectedId, onSelect, page, onPageChange }: CategoryPagerProps) {
  // T19-5（REQ-76①）：按 parentId 自行分组——父类不在本列表内的孤儿按一级处理
  // （改动前它们本来就在宫格里可直接选中，别让它们消失）
  const { topLevel, childrenOf } = useMemo(() => {
    const known = new Set(categories.map(c => c.id))
    const tops: CategoryData[] = []
    const kids = new Map<string, CategoryData[]>()
    for (const cat of categories) {
      if (cat.parentId && known.has(cat.parentId)) {
        const siblings = kids.get(cat.parentId)
        if (siblings) siblings.push(cat)
        else kids.set(cat.parentId, [cat])
      } else {
        tops.push(cat)
      }
    }
    return { topLevel: tops, childrenOf: kids }
  }, [categories])

  // 每页 10 格（2×5），末页补空占位保证两态等高
  const pages = useMemo(() => {
    const result: CategoryData[][] = []
    for (let i = 0; i < topLevel.length; i += 10) {
      result.push(topLevel.slice(i, i + 10))
    }
    return result
  }, [topLevel])
  const safePage = Math.min(page, Math.max(0, pages.length - 1))

  // 展开态：用户最近点亮的一级；若当前选中的是二级，则其父恒展开——「选的是谁的孩子」
  // 始终看得见（收起只能由点一级格触发，而那必然把选中改回一级）。
  const [userExpandedId, setUserExpandedId] = useState<string | null>(null)
  const selectedParentId = useMemo(
    () => categories.find(c => c.id === selectedId)?.parentId ?? null,
    [categories, selectedId],
  )
  const expandedId =
    selectedParentId && childrenOf.has(selectedParentId) ? selectedParentId : userExpandedId
  const expandedChildren = expandedId ? childrenOf.get(expandedId) ?? [] : []

  // 决策 #11：一级仍是合法终值——点一级格只选它（不下钻），展开与否是纯可见性。
  // 已展开时再点＝收起，与 REQ-76 的交互口径一致。
  const handleTopLevelClick = useCallback((cat: CategoryData) => {
    onSelect(cat.id)
    setUserExpandedId(expandedId === cat.id ? null : cat.id)
  }, [expandedId, onSelect])

  // touch 横滑翻页：起点/终点 dx 阈值 ±40px
  const swipeStartXRef = useRef<number | null>(null)
  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    swipeStartXRef.current = e.touches[0]?.clientX ?? null
  }, [])
  const handleTouchEnd = useCallback((e: React.TouchEvent) => {
    if (swipeStartXRef.current === null) return
    const dx = (e.changedTouches[0]?.clientX ?? 0) - swipeStartXRef.current
    swipeStartXRef.current = null
    if (dx <= -40 && safePage < pages.length - 1) onPageChange(safePage + 1)
    else if (dx >= 40 && safePage > 0) onPageChange(safePage - 1)
  }, [safePage, pages.length, onPageChange])

  return (
    <>
      <div
        className="overflow-hidden"
        data-testid="category-pager"
        onTouchStart={handleTouchStart}
        onTouchEnd={handleTouchEnd}
      >
        <div
          className="flex transition-transform duration-200"
          style={{ transform: `translateX(-${safePage * 100}%)` }}
        >
          {pages.map((pg, pi) => (
            <div key={pi} className="w-full shrink-0 grid grid-cols-5 gap-2">
              {pg.map(cat => {
                const hasChildren = childrenOf.has(cat.id)
                // 选中二级时其一级格也点亮（REQ-76 修复设计 ③）
                const active = selectedId === cat.id || selectedParentId === cat.id
                return (
                  <button
                    key={cat.id}
                    onClick={() => handleTopLevelClick(cat)}
                    className={`cat-cell text-xs ${active ? 'active' : ''}`}
                    style={{
                      background: active ? 'var(--glass)' : 'transparent',
                      position: 'relative',
                    }}
                  >
                    {hasChildren && (
                      <span
                        data-testid="cat-expand-marker"
                        aria-hidden
                        style={{
                          position: 'absolute',
                          top: 4,
                          right: 6,
                          fontSize: 9,
                          lineHeight: 1,
                          opacity: 0.55,
                          transform: expandedId === cat.id ? 'rotate(180deg)' : undefined,
                          transition: 'transform 0.15s',
                        }}
                      >
                        ▾
                      </span>
                    )}
                    <CategoryIcon icon={cat.icon} name={cat.name} className="text-lg" />
                    <span className="truncate max-w-full text-ink2">{cat.name}</span>
                  </button>
                )
              })}
              {/* 末页补隐形占位格（与真实格同构，T9-1/UI-18：空 div 行高为 0 等于没补） */}
              {Array.from({ length: 10 - pg.length }).map((_, i) => (
                <div
                  key={`ph-${i}`}
                  data-testid="cat-cell-placeholder"
                  className="cat-cell text-xs"
                  style={{ visibility: 'hidden' }}
                  aria-hidden
                >
                  <CategoryIcon name="占" className="text-lg" />
                  <span className="text-xs">占</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
      {/* 二级面板：内嵌容器 + 从属视觉（REQ-76①）。置于宫格之外——面板上的滑动不翻页 */}
      {expandedChildren.length > 0 && (
        <div
          className="mt-2 rounded-xl p-2"
          data-testid="category-subpanel"
          style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
        >
          <div className="grid grid-cols-5 gap-2">
            {expandedChildren.map(child => (
              <button
                key={child.id}
                onClick={() => onSelect(child.id)}
                className={`cat-cell text-xs ${selectedId === child.id ? 'active' : ''}`}
                style={{ background: selectedId === child.id ? 'var(--glass)' : 'transparent' }}
              >
                <CategoryIcon icon={child.icon} name={child.name} className="text-lg" />
                <span className="truncate max-w-full text-ink2">{child.name}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      {pages.length > 1 && (
        <div className="flex justify-center gap-1.5 mt-2" data-testid="category-page-dots">
          {pages.map((_, i) => (
            <button
              key={i}
              aria-label={`分类第 ${i + 1} 页`}
              onClick={() => onPageChange(i)}
              className="rounded-full"
              style={{
                width: i === safePage ? 16 : 6,
                height: 6,
                background: i === safePage ? 'var(--accent)' : 'var(--glass-border)',
                transition: 'width 0.15s',
              }}
            />
          ))}
        </div>
      )}
    </>
  )
}

// ─── EntryChipRow ──────────────────────────────────────────────────────

export interface EntryChipRowProps {
  books: BookData[]
  accounts: AccountData[]
  selectedBookId: string | null
  selectedAccountId: string | null
  includeInBudget: boolean
  onBookSelect: (id: string) => void
  onAccountSelect: (id: string | null) => void
  onBudgetSelect: (v: boolean) => void
}

/** QuickEntrySheet（REQ-9 / REQ-17）原 :584-702 段：三 chip + 单开下拉。 */
export function EntryChipRow({
  books, accounts,
  selectedBookId, selectedAccountId, includeInBudget,
  onBookSelect, onAccountSelect, onBudgetSelect,
}: EntryChipRowProps) {
  const [openPicker, setOpenPicker] = useState<'book' | 'account' | 'budget' | null>(null)

  const selectableBooks = books.filter(b => !b.isMaster)
  const bookLabel = selectedBookId
    ? (books.find(b => b.id === selectedBookId)?.name || '账本')
    : (pickEntryBook(books)?.name || '选择账本')
  const accountLabel = selectedAccountId
    ? (accounts.find(a => a.id === selectedAccountId)?.name || '账户')
    : (accounts.length > 0 ? '不使用账户' : '无账户')
  // T9-6（决策 #6）：按钮/下拉显示所选实体 icon（照片版经 CategoryIcon 渲染）
  const currentBook = selectedBookId
    ? books.find(b => b.id === selectedBookId)
    : pickEntryBook(books)
  const currentAccount = selectedAccountId
    ? accounts.find(a => a.id === selectedAccountId)
    : undefined

  return (
    <>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => setOpenPicker(openPicker === 'book' ? null : 'book')}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs transition-colors"
          style={{ background: 'var(--glass)', color: 'var(--ink2)', border: '1px solid var(--glass-border)' }}
          data-testid="entry-book-picker"
        >
          <CategoryIcon icon={currentBook?.icon} name={bookLabel} fallback="📒" />
          <span className="max-w-[7rem] truncate">{bookLabel}</span>
          <span aria-hidden style={{ opacity: 0.6 }}>▾</span>
        </button>
        <button
          type="button"
          onClick={() => setOpenPicker(openPicker === 'account' ? null : 'account')}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs transition-colors"
          style={{ background: 'var(--glass)', color: 'var(--ink2)', border: '1px solid var(--glass-border)' }}
          data-testid="entry-account-picker"
        >
          <CategoryIcon icon={currentAccount?.icon} name={accountLabel} fallback="💳" />
          <span className="max-w-[7rem] truncate">{accountLabel}</span>
          <span aria-hidden style={{ opacity: 0.6 }}>▾</span>
        </button>
        {/* 第十轮 REQ-17：是否计入预算（初始不计入） */}
        <button
          type="button"
          onClick={() => setOpenPicker(openPicker === 'budget' ? null : 'budget')}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs transition-colors"
          style={{ background: 'var(--glass)', color: 'var(--ink2)', border: '1px solid var(--glass-border)' }}
          data-testid="entry-budget-picker"
        >
          <span aria-hidden>📊</span>
          <span>{includeInBudget ? '计入预算' : '不计入预算'}</span>
          <span aria-hidden style={{ opacity: 0.6 }}>▾</span>
        </button>
      </div>

      {openPicker === 'book' && (
        <div
          className="mt-2 rounded-xl p-1 max-h-48 overflow-y-auto"
          style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
          data-testid="entry-book-list"
        >
          {selectableBooks.length === 0 ? (
            <div className="px-3 py-2 text-xs text-ink3">暂无可用账本</div>
          ) : (
            selectableBooks.map(b => (
              <button
                key={b.id}
                type="button"
                onClick={() => { onBookSelect(b.id); setOpenPicker(null) }}
                className="w-full text-left px-3 py-2 rounded-lg text-sm flex items-center justify-between"
                style={{ color: selectedBookId === b.id ? 'var(--accent)' : 'var(--ink2)' }}
              >
                <span className="flex items-center gap-1.5 min-w-0">
                  <CategoryIcon icon={b.icon} name={b.name || '账本'} fallback="📒" />
                  <span className="truncate">{b.name || '账本'}</span>
                </span>
                {selectedBookId === b.id && <span>✓</span>}
              </button>
            ))
          )}
        </div>
      )}

      {openPicker === 'account' && (
        <div
          className="mt-2 rounded-xl p-1 max-h-48 overflow-y-auto"
          style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
          data-testid="entry-account-list"
        >
          <button
            type="button"
            onClick={() => { onAccountSelect(null); setOpenPicker(null) }}
            className="w-full text-left px-3 py-2 rounded-lg text-sm flex items-center justify-between"
            style={{ color: selectedAccountId === null ? 'var(--accent)' : 'var(--ink2)' }}
          >
            <span>不使用账户</span>
            {selectedAccountId === null && <span>✓</span>}
          </button>
          {accounts.map(a => (
            <button
              key={a.id}
              type="button"
              onClick={() => { onAccountSelect(a.id); setOpenPicker(null) }}
              className="w-full text-left px-3 py-2 rounded-lg text-sm flex items-center justify-between"
              style={{ color: selectedAccountId === a.id ? 'var(--accent)' : 'var(--ink2)' }}
            >
              <span className="flex items-center gap-1.5 min-w-0">
                <CategoryIcon icon={a.icon} name={a.name} fallback="💰" />
                <span className="truncate">{a.name}</span>
              </span>
              {selectedAccountId === a.id && <span>✓</span>}
            </button>
          ))}
        </div>
      )}

      {openPicker === 'budget' && (
        <div
          className="mt-2 rounded-xl p-1"
          style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
          data-testid="entry-budget-list"
        >
          {[true, false].map(v => (
            <button
              key={String(v)}
              type="button"
              onClick={() => { onBudgetSelect(v); setOpenPicker(null) }}
              className="w-full text-left px-3 py-2 rounded-lg text-sm flex items-center justify-between"
              style={{ color: includeInBudget === v ? 'var(--accent)' : 'var(--ink2)' }}
              data-testid={v ? 'entry-budget-include' : 'entry-budget-exclude'}
            >
              <span>{v ? '计入预算' : '不计入预算'}</span>
              {includeInBudget === v && <span>✓</span>}
            </button>
          ))}
        </div>
      )}
    </>
  )
}
