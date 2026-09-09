/**
 * T-M4-04e: 批量确认卡 — 展示图片识别出的多笔交易，支持编辑和批量确认。
 *
 * 流程：图片 → AI 解析 → 批量确认卡 → 执行 add_tx_batch → 链接附件
 */

import { useState, useCallback } from 'react'
import type { ActionProposal, TransactionItem } from '../../domain/action-proposal'
import type { CategoryData } from '../../platform/bridge/bridge-types'
import DateField from '../../components/DateField'

interface BatchConfirmationCardProps {
  proposal: ActionProposal
  categories: CategoryData[]
  /** 是否正在提交（由父组件控制，避免本地 state 永不重置） */
  submitting?: boolean
  onConfirm: (edited: ActionProposal) => void
  onCancel: () => void
}

interface EditableTx extends TransactionItem {
  _id: string  // local key for React list
}

export function BatchConfirmationCard({
  proposal,
  categories,
  submitting = false,
  onConfirm,
  onCancel,
}: BatchConfirmationCardProps) {
  const transactions = proposal.transactions ?? []
  const [txs, setTxs] = useState<EditableTx[]>(() =>
    transactions.map((tx, i) => ({ ...tx, _id: `tx_${i}_${Date.now()}` }))
  )

  const updateTx = useCallback((idx: number, patch: Partial<TransactionItem>) => {
    setTxs(prev => prev.map((tx, i) => i === idx ? { ...tx, ...patch } : tx))
  }, [])

  const removeTx = useCallback((idx: number) => {
    setTxs(prev => prev.filter((_, i) => i !== idx))
  }, [])

  const totalCents = txs.reduce((sum, tx) => sum + tx.amountCents, 0)

  const handleConfirm = useCallback(() => {
    if (submitting || txs.length === 0) return

    const edited: ActionProposal = {
      ...proposal,
      transactions: txs.map(({ _id, ...tx }) => tx),
      requiresConfirmation: false,
    }
    onConfirm(edited)
  }, [proposal, txs, submitting, onConfirm])

  if (transactions.length === 0) return null

  const lowConfidence = (proposal.confidence ?? 0) < 0.7

  return (
    <div
      className="rounded-xl mx-4 my-2 overflow-hidden"
      style={{
        background: 'var(--glass)',
        border: '1px solid var(--glass-border)',
      }}
      data-testid="batch-confirmation-card"
    >
      {/* Header */}
      <div className="px-4 pt-3 pb-2 flex items-center justify-between">
        <span className="text-sm font-medium" style={{ color: 'var(--ink2)' }}>
          📷 识别到 {transactions.length} 笔交易
        </span>
        {lowConfidence && (
          <span
            className="text-xs px-2 py-0.5 rounded-full"
            style={{
              background: 'var(--warning-bg, #fff3cd)',
              color: 'var(--warning-text, #856404)',
            }}
          >
            低置信度
          </span>
        )}
      </div>

      {/* Transaction list */}
      <div className="px-4 pb-2 max-h-48 overflow-y-auto">
        {txs.map((tx, idx) => (
          <BatchTxRow
            key={tx._id}
            tx={tx}
            index={idx}
            categories={categories}
            onUpdate={updateTx}
            onRemove={removeTx}
          />
        ))}
      </div>

      {/* Total */}
      <div
        className="px-4 py-2 flex items-center justify-between text-sm"
        style={{ borderTop: '1px solid var(--glass-border)' }}
      >
        <span style={{ color: 'var(--ink3)' }}>合计</span>
        <span className="font-bold" style={{ color: 'var(--ink)' }}>
          ¥{(totalCents / 100).toFixed(2)}
        </span>
      </div>

      {/* Warnings */}
      {proposal.warnings.length > 0 && (
        <div className="px-4 pb-2 text-xs" style={{ color: 'var(--warning-text, #856404)' }}>
          {proposal.warnings.map((w, i) => (
            <div key={i}>⚠️ {w}</div>
          ))}
        </div>
      )}

      {/* Actions */}
      <div className="px-4 pb-3 flex gap-2">
        <button
          onClick={onCancel}
          disabled={submitting}
          className="flex-1 py-2 rounded-lg text-sm font-medium"
          style={{
            background: 'transparent',
            color: 'var(--ink2)',
            border: '1px solid var(--glass-border)',
          }}
          data-testid="batch-cancel"
        >
          取消
        </button>
        <button
          onClick={handleConfirm}
          disabled={submitting || txs.length === 0}
          className="flex-1 py-2 rounded-lg text-sm font-medium text-white"
          style={{
            background: submitting ? 'var(--ink3)' : 'var(--accent)',
            opacity: txs.length === 0 ? 0.5 : 1,
          }}
          data-testid="batch-confirm"
        >
          {submitting ? '记账中…' : `确认 ${txs.length} 笔`}
        </button>
      </div>
    </div>
  )
}

// ─── Single editable row ───

interface BatchTxRowProps {
  tx: EditableTx
  index: number
  categories: CategoryData[]
  onUpdate: (idx: number, patch: Partial<TransactionItem>) => void
  onRemove: (idx: number) => void
}

function BatchTxRow({ tx, index, categories, onUpdate, onRemove }: BatchTxRowProps) {
  const [expanded, setExpanded] = useState(false)
  const [amountYuan, setAmountYuan] = useState((tx.amountCents / 100).toFixed(2))

  const filteredCats = categories.filter(c => c.kind === tx.type)
  const catName = categories.find(c => c.id === tx.categoryId)?.name || '未分类'

  const handleAmountBlur = useCallback(() => {
    const cents = Math.round(parseFloat(amountYuan) * 100)
    if (!isNaN(cents) && cents >= 1) {
      onUpdate(index, { amountCents: cents })
    } else {
      setAmountYuan((tx.amountCents / 100).toFixed(2))
    }
  }, [amountYuan, index, tx.amountCents, onUpdate])

  return (
    <div
      className="py-2"
      style={{ borderBottom: '1px solid var(--glass-border)' }}
    >
      {/* Summary row — always visible */}
      <div className="flex items-center gap-2">
        <span className="text-xs" style={{ color: tx.type === 'expense' ? 'var(--expense)' : 'var(--income)' }}>
          {tx.type === 'expense' ? '支出' : '收入'}
        </span>
        <span className="flex-1 text-sm font-medium truncate" style={{ color: 'var(--ink)' }}>
          ¥{amountYuan}
        </span>
        <span className="text-xs truncate max-w-20" style={{ color: 'var(--ink3)' }}>
          {catName}
        </span>
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-xs px-1.5 py-0.5 rounded"
          style={{ color: 'var(--ink3)' }}
        >
          {expanded ? '收起' : '编辑'}
        </button>
        <button
          onClick={() => onRemove(index)}
          className="text-xs px-1"
          style={{ color: 'var(--expense, #e74c3c)' }}
          title="移除此笔"
        >
          ✕
        </button>
      </div>

      {/* Expanded edit form */}
      {expanded && (
        <div className="mt-2 space-y-2">
          {/* Amount */}
          <div className="flex items-center gap-1">
            <label className="text-xs w-10" style={{ color: 'var(--ink3)' }}>金额</label>
            <div className="flex items-center gap-0.5 flex-1">
              <span className="text-sm" style={{ color: 'var(--ink)' }}>¥</span>
              <input
                type="number"
                value={amountYuan}
                onChange={e => setAmountYuan(e.target.value)}
                onBlur={handleAmountBlur}
                className="flex-1 bg-transparent text-sm font-bold outline-none"
                style={{ color: 'var(--ink)' }}
                min="0.01"
                step="0.01"
              />
            </div>
          </div>

          {/* Type */}
          <div className="flex items-center gap-1">
            <label className="text-xs w-10" style={{ color: 'var(--ink3)' }}>类型</label>
            <div className="flex gap-1">
              {(['expense', 'income'] as const).map(t => (
                <button
                  key={t}
                  onClick={() => { onUpdate(index, { type: t, categoryId: undefined }); setAmountYuan(amountYuan) }}
                  className="text-xs px-2 py-1 rounded"
                  style={{
                    background: tx.type === t
                      ? (t === 'expense' ? 'var(--expense)' : 'var(--income)')
                      : 'var(--glass)',
                    color: tx.type === t ? 'white' : 'var(--ink2)',
                  }}
                >
                  {t === 'expense' ? '支出' : '收入'}
                </button>
              ))}
            </div>
          </div>

          {/* Category */}
          <div>
            <label className="text-xs" style={{ color: 'var(--ink3)' }}>分类</label>
            <div className="flex flex-wrap gap-1 mt-1">
              {filteredCats.map(cat => (
                <button
                  key={cat.id}
                  onClick={() => onUpdate(index, { categoryId: cat.id })}
                  className="text-xs px-2 py-1 rounded"
                  style={{
                    background: tx.categoryId === cat.id ? 'var(--accent)' : 'var(--glass)',
                    color: tx.categoryId === cat.id ? 'white' : 'var(--ink2)',
                    border: '1px solid var(--glass-border)',
                  }}
                >
                  {cat.name}
                </button>
              ))}
            </div>
          </div>

          {/* Date */}
          <div className="flex items-center gap-1">
            <label className="text-xs w-10" style={{ color: 'var(--ink3)' }}>日期</label>
            <DateField
              value={tx.bizDate}
              onChange={v => onUpdate(index, { bizDate: v })}
              testId={`batch-date-${index}`}
              className="flex-1 min-w-0"
            />
          </div>

          {/* Note */}
          <div className="flex items-center gap-1">
            <label className="text-xs w-10" style={{ color: 'var(--ink3)' }}>备注</label>
            <input
              type="text"
              value={tx.note}
              onChange={e => onUpdate(index, { note: e.target.value })}
              className="flex-1 px-2 py-1 rounded text-xs"
              style={{
                background: 'var(--glass)',
                color: 'var(--ink)',
                border: '1px solid var(--glass-border)',
              }}
              placeholder="备注（可选）"
            />
          </div>
        </div>
      )}

      {/* Note in collapsed view */}
      {!expanded && tx.note && (
        <div className="text-xs truncate" style={{ color: 'var(--ink3)' }}>
          {tx.note}
        </div>
      )}
    </div>
  )
}
