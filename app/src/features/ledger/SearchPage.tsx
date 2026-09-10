/**
 * SearchPage — 独立搜索子页（第十二轮 T12-5 / REQ-33，决策 #10）。
 *
 * /search 顶级路由，AppLayout 外全屏子页（与 /assistant 同级），PageHeader 返回。
 * 范围 = 当前账本全量：listByBook 不带期间（该命令无强制期间参数，无需 90 天窗口回退）；
 * 账本沿用明细页所选（router state 传入），直达/深链无 state 时回退 pickEntryBook 口径；
 * master 账本与明细页同口径——不查 master，合并其余普通账本。
 * 过滤走 domain/search.ts filterTx 唯一实现；结果行点击 = TxEditSheet 编辑（T12-10）。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useLocation } from 'react-router'
import { PageHeader } from '../../components/Layout'
import { EmptyState } from '../../components/StateViews'
import { CategoryIcon } from '../../components/CategoryIcon'
import { useBridge } from '../../platform/hooks/use-bridge'
import { pickEntryBook } from '../../domain/books'
import { filterTx } from '../../domain/search'
import { TxEditSheet } from './TxEditSheet'
import * as Money from '../../domain/money'
import type { BookData, CategoryData, TransactionData } from '../../platform/bridge/bridge-types'

function fmtAmount(cents: number): string {
  return Money.formatYuan(Money.cents(cents))
}

export function SearchPage() {
  const bridge = useBridge()
  const location = useLocation()
  const [txs, setTxs] = useState<TransactionData[]>([])
  const [categories, setCategories] = useState<CategoryData[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  // T12-10（REQ-36）：结果行点击开编辑弹层
  const [editTx, setEditTx] = useState<TransactionData | null>(null)

  // 当前账本 = 明细页跳转时携带的所选账本；无 state（直达）回退默认账本口径
  const routeBookId = (location.state as { bookId?: string } | null)?.bookId ?? null

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const [bs, cats, meta] = await Promise.all([
        bridge.call('book.list', {}) as Promise<BookData[]>,
        bridge.call('category.list', {}) as Promise<CategoryData[]>,
        bridge.getMeta(['default_book_id']).catch(() => ({} as Record<string, string | null>)),
      ])
      setCategories(cats)
      const target = bs.find(b => b.id === routeBookId) ?? pickEntryBook(bs, meta['default_book_id'] ?? null)
      // master 不存流水（决策 #1）→ 合并其余普通账本，与 DetailPage.loadTxs 同口径
      const targets = target?.isMaster
        ? bs.filter(b => b.id !== target.id && !b.isMaster)
        : target
          ? [target]
          : []
      const lists = await Promise.all(
        targets.map(b => bridge.call('transaction.listByBook', { bookId: b.id }) as Promise<TransactionData[]>),
      )
      setTxs(lists.flat().filter(tx => tx.deletedAt === null))
    } catch (err) {
      console.error('Failed to load search data:', err)
    } finally {
      setLoading(false)
    }
  }, [bridge, routeBookId])

  useEffect(() => {
    reload()
  }, [reload])

  // filterTx 空 query 返回原数组引用 → 先浅拷贝再排，避免 sort 原地改 state
  const results = useMemo(
    () => [...filterTx(txs, categories, query)].sort((a, b) => b.occurredAt - a.occurredAt),
    [txs, categories, query],
  )
  const searching = query.trim().length > 0

  return (
    <div className="min-h-screen pb-20">
      <PageHeader back />

      {/* ─── 搜索条（样式/placeholder 沿用原明细页内联实现） ─── */}
      <div className="px-4 pt-1 pb-1">
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
            autoFocus
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
            找到 {results.length} 笔相关记录
          </div>
        )}
      </div>

      {/* ─── 结果列表（简版明细行：图标 + 名称/日期 + 金额） ─── */}
      <div className="px-4">
        {loading ? (
          <div className="flex justify-center py-12">
            <div className="w-8 h-8 rounded-full border-2 border-t-transparent animate-spin" style={{ borderColor: 'var(--accent)', borderTopColor: 'transparent' }} />
          </div>
        ) : results.length === 0 ? (
          searching
            ? <EmptyState icon="🔍" title="未找到相关记录" />
            : <EmptyState icon="📋" title="暂无记录" />
        ) : (
          results.map((tx, i) => {
            const cat = categories.find(c => c.id === tx.categoryId)
            const note = tx.note?.trim()
            const title = note || cat?.name || '未分类'
            return (
              <button
                key={tx.id}
                data-testid="search-result-row"
                onClick={() => setEditTx(tx)}
                className="flex w-full items-center gap-3 py-2.5 text-left active:opacity-70"
                style={i < results.length - 1 ? { borderBottom: '1px solid var(--glass-border)' } : undefined}
              >
                <div
                  className="flex items-center justify-center shrink-0 rounded-xl"
                  style={{ width: 40, height: 40, background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
                >
                  <CategoryIcon icon={cat?.icon} name={cat?.name ?? ''} className="text-[19px] leading-none" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-[15px] font-semibold truncate" style={{ color: 'var(--ink)' }}>{title}</div>
                  <div className="text-xs truncate mt-0.5" style={{ color: 'var(--ink3)' }}>{tx.bizDate}</div>
                </div>
                <span className="money-num text-[17px] font-bold shrink-0" style={{ color: 'var(--ink)' }}>
                  {tx.type === 'expense' ? '-' : '+'}{fmtAmount(tx.amountCents)}
                </span>
              </button>
            )
          })
        )}
      </div>

      {/* T12-10（REQ-36）：结果行点击开编辑弹层，保存/删除后刷新 */}
      {editTx && (
        <TxEditSheet
          tx={editTx}
          categories={categories}
          onClose={() => setEditTx(null)}
          onSaved={reload}
        />
      )}
    </div>
  )
}
