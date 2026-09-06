/**
 * T-M4-02c: 确认卡 — 展示 AI/本地 NLU 解析的交易提案。
 *
 * 确认前可编辑金额、分类、日期。
 */

import { useState, useCallback } from 'react'
import type { ActionProposal, TransactionItem } from '../../domain/action-proposal'
import type { CategoryData } from '../../platform/bridge/bridge-types'

interface ConfirmationCardProps {
  proposal: ActionProposal
  categories: CategoryData[]
  onConfirm: (edited: ActionProposal) => void
  onCancel: () => void
}

export function ConfirmationCard({ proposal, categories, onConfirm, onCancel }: ConfirmationCardProps) {
  const tx = proposal.transactions?.[0]
  if (!tx) return null
  // 早期返回必须放在 hooks 之前：提取内层组件承载 hooks，避免 rules-of-hooks 报错。
  return (
    <ConfirmationCardBody
      proposal={proposal}
      categories={categories}
      onConfirm={onConfirm}
      onCancel={onCancel}
      tx={tx}
    />
  )
}

function ConfirmationCardBody({
  proposal, categories, onConfirm, onCancel, tx,
}: ConfirmationCardProps & { tx: TransactionItem }) {
  const [amountYuan, setAmountYuan] = useState((tx.amountCents / 100).toFixed(2))
  const [categoryId, setCategoryId] = useState(tx.categoryId || '')
  const [bizDate, setBizDate] = useState(tx.bizDate)
  const [note, setNote] = useState(tx.note)
  const [txType, setTxType] = useState(tx.type)

  const filteredCats = categories.filter(c => c.kind === txType)

  // C-B2 fix: 行内校验错误状态
  const [amountError, setAmountError] = useState<string | null>(null)

  const handleConfirm = useCallback(() => {
    // C-M2 fix: 使用字符串解析避免浮点精度问题
    const amountCents = Math.round(parseFloat(amountYuan) * 100)
    if (isNaN(amountCents) || amountCents < 1) {
      setAmountError('金额必须大于 0')
      return
    }
    setAmountError(null)

    const editedTx: TransactionItem = {
      ...tx,
      type: txType,
      amountCents,
      categoryId: categoryId || undefined,
      bizDate,
      note,
    }

    const editedProposal: ActionProposal = {
      ...proposal,
      transactions: [editedTx],
      requiresConfirmation: false,
    }
    onConfirm(editedProposal)
  }, [proposal, tx, txType, amountYuan, categoryId, bizDate, note, onConfirm])

  return (
    <div
      className="rounded-xl p-4 mx-4 my-2"
      style={{
        background: 'var(--glass)',
        border: '1px solid var(--glass-border)',
      }}
    >
      {/* 标题 */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-medium" style={{ color: 'var(--ink2)' }}>
          确认交易
        </span>
        {proposal.warnings.length > 0 && (
          <span className="text-xs px-2 py-0.5 rounded-full" style={{
            background: 'var(--warning-bg, #fff3cd)',
            color: 'var(--warning-text, #856404)',
          }}>
            {proposal.warnings.length} 个警告
          </span>
        )}
      </div>

      {/* 收支切换 */}
      <div className="flex gap-2 mb-3">
        {(['expense', 'income'] as const).map(t => (
          <button
            key={t}
            onClick={() => { setTxType(t); setCategoryId('') }}
            className="flex-1 py-1.5 rounded-lg text-xs font-medium transition-colors"
            style={{
              background: txType === t
                ? (t === 'expense' ? 'var(--expense)' : 'var(--income)')
                : 'transparent',
              color: txType === t ? 'white' : 'var(--ink2)',
              border: txType === t ? 'none' : '1px solid var(--glass-border)',
            }}
          >
            {t === 'expense' ? '支出' : '收入'}
          </button>
        ))}
      </div>

      {/* 金额 */}
      <div className="mb-3">
        <label className="text-xs mb-1 block" style={{ color: 'var(--ink3)' }}>金额</label>
        <div className="flex items-center gap-1">
          <span className="text-lg" style={{ color: 'var(--ink)' }}>¥</span>
          <input
            type="number"
            value={amountYuan}
            onChange={e => { setAmountYuan(e.target.value); setAmountError(null) }}
            className="flex-1 bg-transparent text-2xl font-bold outline-none"
            style={{ color: 'var(--ink)' }}
            min="0.01"
            step="0.01"
          />
        </div>
        {amountError && (
          <span className="text-xs mt-1 block" style={{ color: 'var(--expense, #e74c3c)' }}>
            {amountError}
          </span>
        )}
      </div>

      {/* 分类 */}
      <div className="mb-3">
        <label className="text-xs mb-1 block" style={{ color: 'var(--ink3)' }}>分类</label>
        <div className="grid grid-cols-5 gap-1.5 max-h-24 overflow-y-auto">
          {filteredCats.map(cat => (
            <button
              key={cat.id}
              onClick={() => setCategoryId(cat.id)}
              className="text-xs py-1.5 rounded-lg transition-colors truncate"
              style={{
                background: categoryId === cat.id ? 'var(--accent)' : 'var(--glass)',
                color: categoryId === cat.id ? 'white' : 'var(--ink2)',
                border: '1px solid var(--glass-border)',
              }}
            >
              {cat.name}
            </button>
          ))}
        </div>
      </div>

      {/* 日期 */}
      <div className="mb-3">
        <label className="text-xs mb-1 block" style={{ color: 'var(--ink3)' }}>日期</label>
        <input
          type="date"
          value={bizDate}
          onChange={e => setBizDate(e.target.value)}
          className="w-full px-2 py-1.5 rounded-lg text-sm outline-none"
          style={{
            background: 'var(--glass)',
            color: 'var(--ink)',
            border: '1px solid var(--glass-border)',
          }}
        />
      </div>

      {/* 备注 */}
      <div className="mb-3">
        <label className="text-xs mb-1 block" style={{ color: 'var(--ink3)' }}>备注</label>
        <input
          type="text"
          value={note}
          onChange={e => setNote(e.target.value)}
          className="w-full px-2 py-1.5 rounded-lg text-sm outline-none"
          style={{
            background: 'var(--glass)',
            color: 'var(--ink)',
            border: '1px solid var(--glass-border)',
          }}
        />
      </div>

      {/* 警告 */}
      {proposal.warnings.length > 0 && (
        <div className="mb-3 text-xs" style={{ color: 'var(--warning-text, #856404)' }}>
          {proposal.warnings.map((w, i) => (
            <div key={i}>⚠️ {w}</div>
          ))}
        </div>
      )}

      {/* 操作按钮 */}
      <div className="flex gap-2">
        <button
          onClick={onCancel}
          className="flex-1 py-2 rounded-lg text-sm font-medium"
          style={{
            background: 'transparent',
            color: 'var(--ink2)',
            border: '1px solid var(--glass-border)',
          }}
        >
          取消
        </button>
        <button
          onClick={handleConfirm}
          className="flex-1 py-2 rounded-lg text-sm font-medium text-white"
          style={{ background: 'var(--accent)' }}
        >
          确认记账
        </button>
      </div>
    </div>
  )
}
