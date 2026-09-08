/**
 * BooksPage — 账本管理页。
 *
 * 列表展示 + 新增账本。
 */

import { useState, useEffect, useCallback } from 'react'
import { PageHeader } from '../../components/Layout'
import { useBridge } from '../../platform/hooks/use-bridge'
import { newUuid } from '../../platform/uuid'
import type { BookData } from '../../platform/bridge/bridge-types'

const BOOK_ICONS = ['📒', '📗', '📘', '📙', '🏠', '✈️', '🛒', '💼']

export function BooksPage() {
  const bridge = useBridge()
  const [books, setBooks] = useState<BookData[]>([])
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(false)
  const [newName, setNewName] = useState('')
  const [newIcon, setNewIcon] = useState('📒')
  const [error, setError] = useState<string | null>(null)

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

  const handleAdd = async () => {
    if (!newName.trim()) return
    setError(null)
    // FIX-006 / BUG-007: 同名检测
    const normalizedName = newName.trim().toLowerCase()
    const exists = books.some(b => b.name.toLowerCase() === normalizedName)
    if (exists) {
      setError('该账本名已存在')
      return
    }
    // 服务端 sync/ai 契约要求 UUID（第六轮 Bug-5.2）
    const id = newUuid()
    try {
      // 必须走 insertBook 封装：native 侧期望 {book, mutationId} 包裹结构
      await bridge.insertBook(
        { id, ownerId: 'local', name: newName.trim(), icon: newIcon, version: 1 },
        newUuid()
      )
      setNewName('')
      setNewIcon('📒')
      setShowAdd(false)
      await load()
    } catch (err) {
      console.error('Failed to add book:', err)
      setError('创建账本失败，请重试。若多次失败请通过意见反馈报告问题')
    }
  }

  return (
    <div className="min-h-screen">
      <PageHeader back />

      <div className="px-4 py-4 space-y-4 pb-20">
        {/* 账本列表 */}
        <div className="glass-card overflow-hidden">
          {books.length === 0 && !loading ? (
            <div className="px-4 py-8 text-center text-sm text-ink3">暂无账本，点击下方添加</div>
          ) : books.length > 0 ? (
            books.map((book, i) => (
              <div key={book.id} className={`flex items-center gap-3 px-4 py-3 ${i < books.length - 1 ? 'row-line' : ''}`}>
                <span className="text-lg">{book.icon || '📒'}</span>
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
                  </div>
                  {book.isMaster && <div className="text-xs text-ink3 mt-0.5">自动汇总，不可记账</div>}
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
            ＋ 添加账本
          </button>
        )}

        {/* 添加表单 */}
        {showAdd && (
          <div className="glass-card p-4 space-y-3">
            <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>新建账本</div>
            <input
              type="text"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              placeholder="账本名称"
              className="w-full px-3 py-2 rounded-lg text-sm outline-none"
              style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
              autoFocus
            />
            <div className="flex gap-2 flex-wrap">
              {BOOK_ICONS.map(icon => (
                <button
                  key={icon}
                  onClick={() => setNewIcon(icon)}
                  className="w-10 h-10 rounded-lg flex items-center justify-center text-lg transition-colors"
                  style={{
                    background: newIcon === icon ? 'var(--accent)' : 'var(--glass)',
                    border: newIcon === icon ? '2px solid var(--accent)' : '1px solid var(--glass-border)',
                  }}
                >
                  {icon}
                </button>
              ))}
            </div>
            {error && (
              <div className="text-xs" style={{ color: 'var(--expense)' }}>{error}</div>
            )}
            <div className="flex gap-2">
              <button
                onClick={() => { setShowAdd(false); setNewName(''); setError(null) }}
                className="flex-1 py-2 rounded-lg text-sm"
                style={{ background: 'var(--glass)', color: 'var(--ink2)' }}
              >
                取消
              </button>
              <button
                onClick={handleAdd}
                disabled={!newName.trim()}
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
