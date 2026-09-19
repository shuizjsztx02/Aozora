/**
 * T-M7-03d（P2）：会话列表抽屉——新建 / 切换 / 重命名 / 删除（架构 §9.2 的
 * 「会话管理：侧边栏会话列表」）。
 *
 * 纯展示件：数据与动作全在 `use-conversations.ts`（拉取、降级、确定性回退）。
 * 这里只负责三件 UI 事：
 *
 *   - **行内重命名**：点 ✎ 就地变输入框（不用 `window.prompt`——WebView 里它可能
 *     被拦，且与四时天空的观感不搭）；
 *   - **行内删除确认**：破坏性操作必过确认（与 OpPreviewCard / ConfirmationCard 同
 *     口径）；服务端的删除是软删 + 清正文，不可撤销；
 *   - **两态提示**：降级（离线 / 未登录 / 旧版本）与流式进行中，各自一行说明——
 *     禁用而不解释等于坏掉，用户会以为是 bug。
 *
 * T-M7-03d 收口：列表里可能有**本机未固化**的会话（`row.serverId === null`，
 * 离线轮 / 失败轮留下的、或「新建」让位下来的那几条）。它们没有服务端行，因此
 * **不给 ✎ / 🗑 入口**（行内用「本机 · 未同步」说明），但可以点开切换。
 */

import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import type { ConversationRow, DegradedReason } from './use-conversations'
import { DEGRADED_HINT } from './use-conversations'

export interface ConversationListProps {
  open: boolean
  onClose: () => void
  rows: ConversationRow[]
  loading: boolean
  degraded: DegradedReason | null
  /** 流式进行中：切换与写操作禁用（一轮仍绑定在当前会话上） */
  busy: boolean
  /** 一次操作失败的可关闭提示 */
  error: string | null
  onCreate: () => void
  /** `id` = 行的 id（本地槽是 null） */
  onSelect: (id: string | null) => void
  /** `id` = **服务端会话 id**（本地会话不给这两个入口） */
  onRename: (id: string, title: string) => void
  onDelete: (id: string) => void
  onDismissError: () => void
}

export function ConversationList({
  open, onClose, rows, loading, degraded, busy, error,
  onCreate, onSelect, onRename, onDelete, onDismissError,
}: ConversationListProps) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editText, setEditText] = useState('')
  const [confirmingId, setConfirmingId] = useState<string | null>(null)

  const hint = busy
    ? '本轮进行中，结束后可切换会话'
    : degraded ? DEGRADED_HINT[degraded] : null
  const writable = !busy && !degraded

  // 重命名 / 删除都以**服务端会话 id** 为准（本地会话没有服务端行，见下）
  const startRename = (row: ConversationRow) => {
    if (row.serverId === null) return
    setConfirmingId(null)
    setEditingId(row.serverId)
    setEditText(row.title)
  }

  const commitRename = () => {
    if (editingId === null) return
    const text = editText.trim()
    setEditingId(null)
    if (text && text !== rows.find(r => r.serverId === editingId)?.title) {
      onRename(editingId, text)
    }
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50"
          data-testid="conversation-drawer"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18 }}
        >
          {/* 遮罩 */}
          <div className="absolute inset-0 bg-black/30" onClick={onClose} />

          <motion.aside
            className="absolute inset-y-0 left-0 w-[78%] max-w-[320px] flex flex-col safe-top"
            style={{
              background: 'var(--nav-bg)',
              backdropFilter: 'blur(var(--blur-amount))',
              borderRight: '1px solid var(--glass-border)',
            }}
            initial={{ x: '-100%' }}
            animate={{ x: 0 }}
            exit={{ x: '-100%' }}
            transition={{ type: 'spring', stiffness: 380, damping: 34 }}
          >
            <div
              className="flex items-center justify-between px-4 py-3"
              style={{ borderBottom: '1px solid var(--glass-border)' }}
            >
              <span className="text-sm font-semibold" style={{ color: 'var(--ink)' }}>
                会话
              </span>
              <button
                type="button"
                onClick={onClose}
                aria-label="关闭会话列表"
                className="w-8 h-8 rounded-full"
                style={{ color: 'var(--ink2)' }}
                data-testid="conversation-drawer-close"
              >
                ✕
              </button>
            </div>

            <div className="px-4 py-3">
              <button
                type="button"
                onClick={() => { onCreate(); onClose() }}
                disabled={!writable}
                className="w-full px-3 py-2 rounded-xl text-sm transition-opacity disabled:opacity-40"
                style={{
                  background: 'var(--glass)',
                  color: 'var(--ink)',
                  border: '1px solid var(--glass-border)',
                }}
                data-testid="conversation-new"
              >
                ＋ 新会话
              </button>
            </div>

            {hint && (
              <div
                className="mx-4 mb-2 px-3 py-1.5 rounded-lg text-xs"
                style={{
                  background: 'var(--glass)',
                  color: 'var(--ink3)',
                  border: '1px solid var(--glass-border)',
                }}
                data-testid="conversation-hint"
              >
                {hint}
              </div>
            )}

            {error && (
              <div
                className="mx-4 mb-2 px-3 py-1.5 rounded-lg text-xs flex items-center justify-between gap-2"
                style={{ background: '#fef2f2', color: '#b91c1c', border: '1px solid #fecaca' }}
                data-testid="conversation-error"
              >
                <span className="truncate">{error}</span>
                <button
                  type="button"
                  onClick={onDismissError}
                  className="shrink-0 underline"
                  data-testid="conversation-error-dismiss"
                >
                  知道了
                </button>
              </div>
            )}

            <div className="flex-1 overflow-y-auto px-2 pb-4" data-testid="conversation-rows">
              {loading && rows.length === 0 && (
                <div className="px-3 py-4 text-xs" style={{ color: 'var(--ink3)' }}>
                  正在加载…
                </div>
              )}
              {!loading && rows.length === 0 && (
                <div className="px-3 py-4 text-xs" style={{ color: 'var(--ink3)' }}>
                  还没有会话
                </div>
              )}

              {rows.map(row => {
                const key = row.id ?? 'local'
                const editing = editingId !== null && editingId === row.serverId
                const confirming = confirmingId !== null && confirmingId === row.serverId
                return (
                  <div
                    key={key}
                    className="px-2 py-2 rounded-xl mb-1"
                    style={{ background: row.active ? 'var(--glass)' : 'transparent' }}
                    data-testid={`conversation-row-${key}`}
                    data-active={row.active ? 'true' : 'false'}
                  >
                    {editing ? (
                      <div className="flex items-center gap-1">
                        <input
                          type="text"
                          value={editText}
                          onChange={e => setEditText(e.target.value)}
                          onKeyDown={e => { if (e.key === 'Enter') commitRename() }}
                          maxLength={120}
                          autoFocus
                          className="flex-1 min-w-0 px-2 py-1 rounded-lg text-sm outline-none"
                          style={{
                            background: 'var(--glass)',
                            color: 'var(--ink)',
                            border: '1px solid var(--glass-border)',
                          }}
                          data-testid="conversation-rename-input"
                        />
                        <button
                          type="button"
                          onClick={commitRename}
                          className="text-xs px-2 py-1 rounded-lg shrink-0"
                          style={{ color: 'var(--accent)' }}
                          data-testid="conversation-rename-save"
                        >
                          保存
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditingId(null)}
                          className="text-xs px-1 py-1 rounded-lg shrink-0"
                          style={{ color: 'var(--ink3)' }}
                          data-testid="conversation-rename-cancel"
                        >
                          取消
                        </button>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => { onSelect(row.id); onClose() }}
                          disabled={busy}
                          className="flex-1 min-w-0 text-left disabled:opacity-60"
                          data-testid={`conversation-select-${key}`}
                        >
                          <div
                            className="text-sm truncate"
                            style={{ color: 'var(--ink)' }}
                          >
                            {row.title}
                          </div>
                          {row.subtitle && (
                            <div className="text-[10px]" style={{ color: 'var(--ink3)' }}>
                              {row.subtitle}
                            </div>
                          )}
                        </button>
                        {/* 本机未固化的会话没有服务端 id，重命名 / 删除都无从谈起
                            （行内副标题已写明「本机 · 未同步」） */}
                        {row.serverId !== null && (
                          <>
                            <button
                              type="button"
                              onClick={() => startRename(row)}
                              disabled={!writable}
                              aria-label={`重命名 ${row.title}`}
                              className="text-xs px-1 shrink-0 disabled:opacity-40"
                              style={{ color: 'var(--ink3)' }}
                              data-testid={`conversation-rename-${key}`}
                            >
                              ✎
                            </button>
                            <button
                              type="button"
                              onClick={() => { setEditingId(null); setConfirmingId(row.serverId) }}
                              disabled={!writable}
                              aria-label={`删除 ${row.title}`}
                              className="text-xs px-1 shrink-0 disabled:opacity-40"
                              style={{ color: 'var(--ink3)' }}
                              data-testid={`conversation-delete-${key}`}
                            >
                              🗑
                            </button>
                          </>
                        )}
                      </div>
                    )}

                    {confirming && (
                      <div
                        className="mt-2 flex items-center justify-between gap-2 text-xs"
                        data-testid={`conversation-delete-confirm-${key}`}
                      >
                        <span style={{ color: 'var(--ink2)' }}>删除这条会话？不可撤销</span>
                        <span className="flex gap-2 shrink-0">
                          <button
                            type="button"
                            onClick={() => setConfirmingId(null)}
                            style={{ color: 'var(--ink3)' }}
                            data-testid={`conversation-delete-cancel-${key}`}
                          >
                            取消
                          </button>
                          <button
                            type="button"
                            onClick={() => { setConfirmingId(null); onDelete(row.serverId!) }}
                            style={{ color: 'var(--expense)' }}
                            data-testid={`conversation-delete-confirm-button-${key}`}
                          >
                            删除
                          </button>
                        </span>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </motion.aside>
        </motion.div>
      )}
    </AnimatePresence>
  )
}