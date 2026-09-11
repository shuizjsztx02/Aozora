/**
 * T12-11（REQ-37 ③ / 决策 #5）：AI edit/delete 意图的预览确认卡。
 *
 * 破坏性操作必过确认（第八轮免确认决策只覆盖新增）：候选 = use-assistant 用
 * LLM filter 条件在当前账本 listByBook 结果本地圈定（决策 #3，LLM 永不出 id）。
 * 预览 ≤OP_PREVIEW_ROWS 行（日期 · 分类 · 金额 · 备注）；执行数截断
 * MAX_BATCH_OPS 并在卡上提示；「执行(N)」触发 hook confirmOp（逐笔写、
 * 部分失败可见），「取消」cancelOp 不落任何写。
 *
 * 第十三轮 T13-5（REQ-48 / 决策 #8）：泛化双形态——config 预览（variant='config'，
 * title + rows[{label,value}] 2-4 行 + 确认/取消 + executing 态）。config 一律
 * 预览确认才写（防呆），确认钮「确认」（单操作无笔数）。
 */

import { useState, type ReactNode } from 'react'
import { CategoryIcon } from '../../components/CategoryIcon'
import { OP_PREVIEW_ROWS, MAX_BATCH_OPS } from '../../domain/ai-proposal-builder'
import type {
  AccountData, BookData, CategoryData, TransactionData,
} from '../../platform/bridge/bridge-types'
import type { AiOpSet } from '../../domain/ai-proposal-builder'
import type { PendingOp } from './use-assistant'

interface PreviewActions {
  onConfirm: () => Promise<void> | void
  onCancel: () => void
}

/** T12-11 原形态：edit/delete 交易候选列表预览（variant 缺省）。
 *  T15-7（REQ-56 ④）：accounts/books 名单下传 OpRow——补账户/账本/收支类型/
 *  计入预算呈现（仅被改或有值时）；缺省空名单 = 旧渲染行为。 */
interface TxPreviewProps extends PreviewActions {
  variant?: 'transactions'
  op: PendingOp
  categories: CategoryData[]
  accounts?: AccountData[]
  books?: BookData[]
}

/** T13-5：config 设置操作预览（操作类型/目标名/关键参数，≤4 行）。 */
interface ConfigPreviewProps extends PreviewActions {
  variant: 'config'
  title: string
  rows: Array<{ label: string; value: string }>
}

export type OpPreviewCardProps = TxPreviewProps | ConfigPreviewProps

export function OpPreviewCard(props: OpPreviewCardProps) {
  const { onConfirm, onCancel } = props
  const [busy, setBusy] = useState(false)

  const handleConfirm = async () => {
    if (busy) return
    setBusy(true)
    try {
      await onConfirm()
    } finally {
      // 成功后 pendingOp/pendingConfig 清空、本卡卸载（卸载后 setState 无害）；失败保留可重试
      setBusy(false)
    }
  }

  const shell = (titleNode: ReactNode, bodyNode: ReactNode, confirmLabel: string, confirmDanger: boolean) => (
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
          {titleNode}
        </span>
      </div>
      {bodyNode}
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
          style={{ background: confirmDanger ? 'var(--expense)' : 'var(--accent)' }}
          data-testid="op-confirm"
        >
          {busy ? '执行中…' : confirmLabel}
        </button>
      </div>
    </div>
  )

  if (props.variant === 'config') {
    return shell(
      props.title,
      (
        <div className="mt-2">
          {props.rows.map(r => (
            <div
              key={r.label}
              className="flex items-baseline gap-2 py-1 border-b last:border-b-0"
              style={{ borderColor: 'var(--glass-border)' }}
              data-testid="op-config-row"
            >
              <span className="text-xs shrink-0" style={{ color: 'var(--ink2)' }}>{r.label}</span>
              <span className="text-xs flex-1 min-w-0 text-right" style={{ color: 'var(--ink)' }}>
                {r.value}
              </span>
            </div>
          ))}
        </div>
      ),
      '确认',
      false,
    )
  }

  const { op, categories } = props
  const isDelete = op.kind === 'delete'
  const total = op.candidates.length
  const execCount = Math.min(total, MAX_BATCH_OPS)
  const rows = op.candidates.slice(0, OP_PREVIEW_ROWS)

  return shell(
    isDelete ? `🗑 将删除 ${execCount} 笔记录` : `✏️ 将修改 ${execCount} 笔记录`,
    (
      <>
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
            <OpRow
              key={tx.id} tx={tx} categories={categories}
              accounts={props.accounts ?? []} books={props.books ?? []}
              set={op.kind === 'edit' ? op.set : null}
            />
          ))}
        </div>
      </>
    ),
    `执行 (${execCount})`,
    isDelete,
  )
}

/**
 * 简版明细行：分类图标 + 名称（一级 · 二级）+ 日期 + 备注 | 金额（收支柱子色）。
 * T15-7（REQ-56 ④）副行——仅被改或有值时呈现：账户/账本当前名、不计入预算标记，
 * edit 时追加「改为」diff（模型报名、预览按名呈现；账户/账本名在 openOpPreview
 * 已过名→id 校验，匹配不到的字段已剔除不会出现在这里）。
 */
function setDiffParts(set: AiOpSet): string[] {
  const parts: string[] = []
  if (set.type) parts.push(`收支·${set.type === 'income' ? '收入' : '支出'}`)
  if (set.amountCents !== undefined) parts.push(`金额·¥${(set.amountCents / 100).toFixed(2)}`)
  if (set.category) parts.push(`分类·${set.category}`)
  if (set.note !== undefined) parts.push(`备注·${set.note || '（清空）'}`)
  if (set.bizDate) parts.push(`日期·${set.bizDate}`)
  if (set.accountName) parts.push(`账户·${set.accountName}`)
  if (set.bookName) parts.push(`账本·${set.bookName}`)
  if (set.includeInBudget !== undefined) parts.push(set.includeInBudget ? '计入预算' : '不计入预算')
  return parts
}

function OpRow({ tx, categories, accounts, books, set }: {
  tx: TransactionData
  categories: CategoryData[]
  accounts: AccountData[]
  books: BookData[]
  set: AiOpSet | null
}) {
  const cat = categories.find(c => c.id === tx.categoryId)
  const parent = cat?.parentId ? categories.find(c => c.id === cat.parentId) : undefined
  const label = cat ? (parent ? `${parent.name} · ${cat.name}` : cat.name) : '未分类'
  const sign = tx.type === 'expense' ? '-' : '+'
  const accName = tx.accountId
    ? accounts.find(a => a.id === tx.accountId)?.name
    : undefined
  const bkName = books.find(b => b.id === tx.bookId)?.name
  const diff = set ? setDiffParts(set) : []
  const subParts = [
    accName ? `账户·${accName}` : '',
    bkName ? `账本·${bkName}` : '',
    tx.includeInBudget === false ? '不计入预算' : '',
  ].filter(Boolean)
  return (
    <div
      className="py-1 border-b last:border-b-0"
      style={{ borderColor: 'var(--glass-border)' }}
      data-testid="op-row"
    >
      <div className="flex items-center gap-2">
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
      {(subParts.length > 0 || diff.length > 0) && (
        <div
          className="text-[10px] mt-0.5 pl-6"
          style={{ color: 'var(--ink3)' }}
          data-testid="op-row-subline"
        >
          {subParts.join(' · ')}
          {diff.length > 0 && (
            <span data-testid="op-row-diff">
              {subParts.length > 0 ? '　' : ''}改为：{diff.join(' · ')}
            </span>
          )}
        </div>
      )}
    </div>
  )
}
