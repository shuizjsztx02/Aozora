/**
 * T12-11（REQ-37 ③ / 决策 #5）：AI edit/delete 意图的预览确认卡。
 *
 * 破坏性操作必过确认（第八轮免确认决策只覆盖新增）：候选 = use-assistant 用
 * LLM filter 条件在当前账本 listByBook 结果本地圈定（决策 #3，LLM 永不出 id）。
 * 预览 ≤OP_PREVIEW_ROWS 行（日期 · 分类 · 金额 · 备注）；执行数截断
 * MAX_BATCH_OPS 并在卡上提示；「执行(N)」触发 hook confirmOp（逐笔写、
 * 部分失败可见），「取消」cancelOp 不落任何写。
 */

import { useState } from 'react'
import { CategoryIcon } from '../../components/CategoryIcon'
import { OP_PREVIEW_ROWS, MAX_BATCH_OPS } from '../../domain/ai-proposal-builder'
import type { CategoryData, TransactionData } from '../../platform/bridge/bridge-types'
import type { PendingOp } from './use-assistant'

interface OpPreviewCardProps {
  op: PendingOp
  categories: CategoryData[]
  onConfirm: () => Promise<void> | void
  onCancel: () => void
}

export function OpPreviewCard({ op, categories, onConfirm, onCancel }: OpPreviewCardProps) {
  const [busy, setBusy] = useState(false)
  const isDelete = op.kind === 'delete'
  const total = op.candidates.length
  const execCount = Math.min(total, MAX_BATCH_OPS)
  const rows = op.candidates.slice(0, OP_PREVIEW_ROWS)

  const handleConfirm = async () => {
    if (busy) return
    setBusy(true)
    try {
      await onConfirm()
    } finally {
      // 成功后 pendingOp 清空、本卡卸载（卸载后 setState 无害）；失败保留可重试
      setBusy(false)
    }
  }

  return (
    <div
      className="rounded-xl p-3 mx-4 my-2"
      style={{
        background: 'var(--glass)',
        border: '1px solid var(--glass-border)',
      }}
      data-testid="op-preview-card"
    >
      {/* 标题行：操作类型 + 执行数（截断后） */}
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium" style={{ color: 'var(--ink)' }} data-testid="op-preview-title">
          {isDelete ? `🗑 将删除 ${execCount} 笔记录` : `✏️ 将修改 ${execCount} 笔记录`}
        </span>
      </div>

      {/* 超批量上限提示（REQ-37 ⑤：v1 单操作 50 笔截断） */}
      {total > MAX_BATCH_OPS && (
        <div className="text-xs mt-1" style={{ color: 'var(--warning-text, #856404)' }} data-testid="op-truncate-hint">
          命中 {total} 笔，超出单次 {MAX_BATCH_OPS} 笔上限，仅执行最近 {MAX_BATCH_OPS} 笔
        </div>
      )}
      {/* 预览行数截断提示 */}
      {rows.length < execCount && (
        <div className="text-xs mt-1" style={{ color: 'var(--ink3)' }}>
          预览仅显示前 {OP_PREVIEW_ROWS} 笔
        </div>
      )}

      {/* 候选明细（bizDate DESC，最新在前——与 listByBook 圈定顺序一致） */}
      <div className="mt-2 max-h-64 overflow-y-auto">
        {rows.map(tx => (
          <OpRow key={tx.id} tx={tx} categories={categories} />
        ))}
      </div>

      {/* 执行 / 取消（决策 #5：确认步骤不可省；执行中禁双触） */}
      <div className="flex gap-2 mt-2">
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="flex-1 py-1.5 rounded-lg text-xs disabled:opacity-40"
          style={{
            background: 'var(--glass)',
            color: 'var(--ink2)',
            border: '1px solid var(--glass-border)',
          }}
          data-testid="op-cancel"
        >
          取消
        </button>
        <button
          type="button"
          onClick={() => void handleConfirm()}
          disabled={busy}
          className="flex-1 py-1.5 rounded-lg text-xs font-medium text-white disabled:opacity-60"
          style={{ background: isDelete ? 'var(--expense)' : 'var(--accent)' }}
          data-testid="op-confirm"
        >
          {busy ? '执行中…' : `执行 (${execCount})`}
        </button>
      </div>
    </div>
  )
}

/** 简版明细行：分类图标 + 名称（一级 · 二级）+ 日期 + 备注 | 金额（收支柱子色）。 */
function OpRow({ tx, categories }: { tx: TransactionData; categories: CategoryData[] }) {
  const cat = categories.find(c => c.id === tx.categoryId)
  const parent = cat?.parentId ? categories.find(c => c.id === cat.parentId) : undefined
  const label = cat ? (parent ? `${parent.name} · ${cat.name}` : cat.name) : '未分类'
  const sign = tx.type === 'expense' ? '-' : '+'
  return (
    <div
      className="flex items-center gap-2 py-1 border-b last:border-b-0"
      style={{ borderColor: 'var(--glass-border)' }}
      data-testid="op-row"
    >
      <CategoryIcon
        icon={cat?.icon ?? parent?.icon}
        name={cat?.name || '💰'}
        className="text-base leading-none shrink-0"
      />
      <span className="text-xs shrink-0" style={{ color: 'var(--ink2)' }}>{tx.bizDate}</span>
      <span className="text-xs truncate flex-1 min-w-0" style={{ color: 'var(--ink)' }}>
        {label}{tx.note ? `｜${tx.note}` : ''}
      </span>
      <span
        className="text-xs font-medium shrink-0"
        style={{ color: tx.type === 'expense' ? 'var(--expense)' : 'var(--income)' }}
      >
        {sign}¥{(tx.amountCents / 100).toFixed(2)}
      </span>
    </div>
  )
}
