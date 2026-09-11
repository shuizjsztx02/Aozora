/**
 * BooksPage — 账本管理页。
 *
 * 列表展示 + 新增账本 + T14-2（REQ-39 / 决策 #3）点击行编辑（照搬 AccountsPage
 * 结构：editing 二态表单 + 长按排序 suppressClickRef 互斥；master 系统账本不给
 * 编辑入口）+ T9-5b（REQ-11）：默认勾选（radio 语义，master 不提供）
 * 与长按进入的 ↑↓ 排序模式（决策 #4）。顺序/默认写 sync_meta（meta.set），
 * 列表顺序与 isDefault 由 native book.list 出口统一整形（决策 #2），前端不再重排。
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { PageHeader } from '../../components/Layout'
import { useBridge } from '../../platform/hooks/use-bridge'
import { newUuid } from '../../platform/uuid'
import { CategoryIcon } from '../../components/CategoryIcon'
import { IconPicker } from '../../components/IconPicker'
import type { BookData } from '../../platform/bridge/bridge-types'
import { META_KEY_BOOK_ORDER, META_KEY_DEFAULT_BOOK_ID, serializeIdOrder } from '../../domain/meta'

export function BooksPage() {
  const bridge = useBridge()
  const [books, setBooks] = useState<BookData[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // T14-2（REQ-39）：新建/编辑共用一个表单 — editing 非空即编辑态（照搬 AccountsPage）
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<BookData | null>(null)
  const [newName, setNewName] = useState('')
  // T9-6（REQ-13）：图标改共享 IconPicker（emoji 宫格 + 相册照片），null=用默认 📒
  const [newIcon, setNewIcon] = useState<string | null>('📒')
  // T9-5b（REQ-11）：长按 500ms 进入排序模式（决策 #4，↑↓ 按钮式，不做拖拽）
  const [reorderMode, setReorderMode] = useState(false)

  const load = useCallback(async () => {
    try {
      const list = await bridge.call('book.list', {}) as BookData[]
      setBooks(list)
    } catch (err) {
      console.error('Failed to load books:', err)
    } finally {
      setLoading(false)
    }
  }, [bridge])

  useEffect(() => { load() }, [load])

  const openBookForm = (book: BookData | null) => {
    if (reorderMode) return // 排序模式禁用进编辑（对齐 AccountsPage 决策 #4）
    setError(null)
    setEditing(book)
    setNewName(book?.name ?? '')
    // 非预置值（照片 data URI 等）由 IconPicker 自动回填对应控件
    setNewIcon(book?.icon ?? '📒')
    setShowForm(true)
  }

  const closeForm = () => {
    setShowForm(false)
    setEditing(null)
    setError(null)
  }

  const handleSubmit = async () => {
    if (!newName.trim()) return
    setError(null)
    // FIX-006 / BUG-007: 同名检测（编辑态排除自身，对齐 AccountsPage）
    const normalizedName = newName.trim().toLowerCase()
    if (books.some(b => b.id !== editing?.id && b.name.toLowerCase() === normalizedName)) {
      setError('该账本名已存在')
      return
    }
    try {
      // 必须走 insertBook/updateBook 封装：native 侧期望 {book, mutationId} 包裹结构
      if (editing) {
        // T14-2（决策 #3）：book.update 局部合并，version 必须递增
        await bridge.updateBook(
          { id: editing.id, ownerId: 'local', name: newName.trim(), icon: newIcon || '📒', version: editing.version + 1 },
          newUuid()
        )
      } else {
        // 服务端 sync/ai 契约要求 UUID（第六轮 Bug-5.2）
        await bridge.insertBook(
          { id: newUuid(), ownerId: 'local', name: newName.trim(), icon: newIcon || '📒', version: 1 },
          newUuid()
        )
      }
      closeForm()
      await load()
    } catch (err) {
      console.error('Failed to save book:', err)
      setError(editing ? '更新账本失败，请重试' : '创建账本失败，请重试。若多次失败请通过意见反馈报告问题')
      // 失败时表单与输入保留，供用户改后重试
    }
  }

  /** 勾默认（单选语义=覆写同键）；总账本不可记账 → 不提供（决策 #4）。 */
  const handleSetDefault = async (book: BookData) => {
    setError(null)
    try {
      await bridge.setMeta(META_KEY_DEFAULT_BOOK_ID, book.id)
      await load()
    } catch (err) {
      console.error('Failed to set default book:', err)
      setError('设置默认账本失败，请重试')
    }
  }

  /** ↑↓ 一次移一格：交换 books 中相邻两项后全量写 order；master 恒首位不参与。 */
  const handleMove = async (index: number, dir: -1 | 1) => {
    const target = index + dir
    const ids = books.map(b => b.id)
    if (target < 0 || target >= ids.length) return
    // 不允许把任何行移入/移出 master 所在首位区（master 由 native 恒置首，本地镜像其序）
    if (books[index].isMaster || books[target].isMaster) return
    ;[ids[index], ids[target]] = [ids[target], ids[index]]
    setError(null)
    try {
      await bridge.setMeta(META_KEY_BOOK_ORDER, serializeIdOrder(ids))
      await load()
    } catch (err) {
      console.error('Failed to persist book order:', err)
      setError('保存顺序失败，请重试')
    }
  }

  // ─── 长按手势（决策 #4）；T14-2：本页面行有 click→编辑，长按触发后需吞掉同手势的 click ───
  const pressRef = useRef<{ timer: number; x: number; y: number } | null>(null)
  const suppressClickRef = useRef(false)
  const onRowPointerDown = (e: React.PointerEvent) => {
    if (reorderMode || showForm) return
    const startX = e.clientX
    const startY = e.clientY
    const timer = window.setTimeout(() => {
      suppressClickRef.current = true
      setReorderMode(true)
    }, 500)
    pressRef.current = { timer, x: startX, y: startY }
  }
  const onRowPointerMove = (e: React.PointerEvent) => {
    const p = pressRef.current
    if (!p) return
    if (Math.abs(e.clientX - p.x) > 10 || Math.abs(e.clientY - p.y) > 10) {
      window.clearTimeout(p.timer)
      pressRef.current = null
    }
  }
  const onRowPointerUp = () => {
    if (pressRef.current) {
      window.clearTimeout(pressRef.current.timer)
      pressRef.current = null
    }
  }
  const takeSuppressedClick = (): boolean => {
    if (suppressClickRef.current) {
      suppressClickRef.current = false
      return true
    }
    return false
  }
  useEffect(() => () => {
    if (pressRef.current) window.clearTimeout(pressRef.current.timer)
  }, [])

  return (
    <div className="min-h-screen">
      <PageHeader back />

      <div className="px-4 py-4 space-y-4 pb-20">
        {/* 排序/默认说明（决策 #4 文案） */}
        {!reorderMode
          ? <div className="text-xs text-center" style={{ color: 'var(--ink3)' }}>勾选的行为默认选择，长按可行调整顺序</div>
          : (
            <div className="flex items-center justify-between">
              <span className="text-xs" style={{ color: 'var(--ink3)' }}>排序模式：↑↓ 调整顺序</span>
              <button
                onClick={() => setReorderMode(false)}
                className="text-xs px-3 py-1.5 rounded-lg font-medium text-white"
                style={{ background: 'var(--accent)' }}
                data-testid="book-reorder-done"
              >
                完成
              </button>
            </div>
          )}

        {/* 账本列表 */}
        <div className="glass-card overflow-hidden">
          {books.length === 0 && !loading ? (
            <div className="px-4 py-8 text-center text-sm text-ink3">暂无账本，点击下方添加</div>
          ) : books.length > 0 ? (
            books.map((book, i) => (
              <div
                key={book.id}
                role="button"
                onClick={() => {
                  if (takeSuppressedClick()) return
                  // T14-2（决策 #3）：master 系统账本不给编辑入口
                  if (book.isMaster) return
                  openBookForm(book)
                }}
                className={`w-full flex items-center gap-3 px-4 py-3 text-left ${i < books.length - 1 ? 'row-line' : ''}`}
                onPointerDown={onRowPointerDown}
                onPointerMove={onRowPointerMove}
                onPointerUp={onRowPointerUp}
                onPointerCancel={onRowPointerUp}
                data-testid={`book-row-${book.id}`}
              >
                {/* T9-6（决策 #6）：照片 icon 是 data URI，裸文本插值会成乱码长串 → CategoryIcon 统一渲染 */}
                <CategoryIcon icon={book.icon} name={book.name || '默认账本'} fallback="📒" className="text-lg" />
                <div className="text-sm flex-1" style={{ color: 'var(--ink)' }}>
                  <div className="flex items-center gap-2">
                    <span>{book.name || '默认账本'}</span>
                    {/* REQ-6 / T7-6：总账本标识（isMaster 由 native book.list 透出） */}
                    {book.isMaster && (
                      <span
                        className="text-[10px] px-1.5 py-0.5 rounded-full shrink-0"
                        style={{ background: 'var(--glass)', color: 'var(--accent)' }}
                      >
                        汇总 · 所有账本
                      </span>
                    )}
                    {/* T9-5b：默认徽标 */}
                    {book.isDefault && !book.isMaster && (
                      <span
                        className="text-[10px] px-1.5 py-0.5 rounded-full shrink-0"
                        style={{ background: 'var(--glass)', color: 'var(--accent)' }}
                        data-testid={`book-default-badge-${book.id}`}
                      >
                        默认
                      </span>
                    )}
                  </div>
                  {book.isMaster && <div className="text-xs text-ink3 mt-0.5">自动汇总，不可记账</div>}
                </div>
                {/* 排序模式：↑↓（master 不渲染）；普通模式：默认勾选（master 不渲染，决策 #4） */}
                {reorderMode && !book.isMaster ? (
                  <div className="flex gap-1 shrink-0">
                    <button
                      type="button"
                      onClick={() => handleMove(i, -1)}
                      disabled={i === 0 || !!books[i - 1]?.isMaster}
                      className="w-8 h-8 rounded-lg text-sm disabled:opacity-30"
                      style={{ background: 'var(--glass)', color: 'var(--ink2)', border: '1px solid var(--glass-border)' }}
                      data-testid={`book-move-up-${book.id}`}
                      aria-label={`上移 ${book.name}`}
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      onClick={() => handleMove(i, 1)}
                      disabled={i === books.length - 1}
                      className="w-8 h-8 rounded-lg text-sm disabled:opacity-30"
                      style={{ background: 'var(--glass)', color: 'var(--ink2)', border: '1px solid var(--glass-border)' }}
                      data-testid={`book-move-down-${book.id}`}
                      aria-label={`下移 ${book.name}`}
                    >
                      ↓
                    </button>
                  </div>
                ) : !book.isMaster ? (
                  <label className="flex items-center shrink-0" onClick={e => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={!!book.isDefault}
                      onChange={() => { void handleSetDefault(book) }}
                      className="w-4 h-4"
                      data-testid={`book-default-${book.id}`}
                      aria-label={`设为默认账本 ${book.name}`}
                    />
                  </label>
                ) : null}
              </div>
            ))
          ) : (
            <div className="px-4 py-3 text-xs text-ink3">加载中…</div>
          )}
        </div>

        {/* 添加按钮（排序模式下隐藏，防混写） */}
        {!showForm && !reorderMode && (
          <button
            onClick={() => openBookForm(null)}
            className="w-full py-3 rounded-lg text-sm font-medium transition-colors"
            style={{ background: 'var(--accent)', color: 'white' }}
          >
            ＋ 添加账本
          </button>
        )}

        {/* 新建 / 编辑表单（T14-2：editing 二态，编辑态仅开放名称 + 图标——
            BookEntity 可编辑字段即此两项；isDefault/master 为 meta 键，不经本表单） */}
        {showForm && (
          <div className="glass-card p-4 space-y-3" data-testid="book-form">
            <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>
              {editing ? '编辑账本' : '新建账本'}
            </div>
            <input
              type="text"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              placeholder="账本名称"
              className="w-full px-3 py-2 rounded-lg text-sm outline-none"
              style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
              autoFocus
            />
            <IconPicker icon={newIcon} onChange={setNewIcon} name={newName} fallback="📒" />
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
                disabled={!newName.trim()}
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
