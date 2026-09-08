/**
 * T-M4-02c: 助手记账卡 — 第八轮 REQ-10/Bug-6 改造。
 *
 * 免确认模式：解析成功后交易已由 use-assistant 直接本地写入，本卡不再是
 * "确认卡"而是**常驻编辑器**——无"确认记账/取消"按钮，任何字段改动即时
 * 触发替换保存（编辑 = softDelete 旧 + insert 新，由上层处理）。
 *
 * 紧凑布局保留 7 要素：分类图标 / 收支类型 / 金额 / 一级分类 / 二级分类 / 日期 / 备注。
 * 金额与备注在失焦时保存（避免每键写库），其余即时保存。
 */

import { useState, useCallback, useRef } from 'react'
import type { ActionProposal, TransactionItem } from '../../domain/action-proposal'
import type { CategoryData } from '../../platform/bridge/bridge-types'
import { CategoryIcon } from '../../components/CategoryIcon'
import type { CardStatus } from './use-assistant'

interface ConfirmationCardProps {
  proposal: ActionProposal
  categories: CategoryData[]
  /** 上层本地写入状态：executing=保存中 committed=已记录 error=保存失败（可改可重试） */
  status: CardStatus
  /** 提交编辑后的完整提案（上层执行替换写） */
  onEditCommit: (edited: ActionProposal) => void
}

/** 已保存值快照，避免同值重复触发替换写。 */
interface CommittedSnapshot {
  type: TransactionItem['type']
  amountCents: number
  categoryId: string
  bizDate: string
  note: string
}

export function ConfirmationCard({ proposal, categories, status, onEditCommit }: ConfirmationCardProps) {
  const tx = proposal.transactions?.[0]
  if (!tx) return null
  // 早期返回必须在 hooks 之前：内层组件承载 hooks
  return (
    <ConfirmationCardBody
      proposal={proposal}
      categories={categories}
      status={status}
      onEditCommit={onEditCommit}
      tx={tx}
    />
  )
}

function ConfirmationCardBody({
  proposal, categories, status, onEditCommit, tx,
}: ConfirmationCardProps & { tx: TransactionItem }) {
  const [amountYuan, setAmountYuan] = useState((tx.amountCents / 100).toFixed(2))
  const [categoryId, setCategoryId] = useState(tx.categoryId || '')
  const [bizDate, setBizDate] = useState(tx.bizDate)
  const [note, setNote] = useState(tx.note)
  const [txType, setTxType] = useState(tx.type)
  const [amountError, setAmountError] = useState<string | null>(null)

  // C-M2 fix: 字符串解析避免浮点精度问题
  const parseAmountCents = (s: string): number | null => {
    const cents = Math.round(parseFloat(s) * 100)
    if (isNaN(cents) || cents < 1) return null
    return cents
  }

  const lastCommittedRef = useRef<CommittedSnapshot>({
    type: tx.type,
    amountCents: tx.amountCents,
    categoryId: tx.categoryId || '',
    bizDate: tx.bizDate,
    note: tx.note,
  })

  /**
   * 构建编辑后提案并（值有变化且合法时）提交。
   * 未点名的字段取**当前本地态**（如支柱已切收入后点分类，type 用新值而非旧快照）；
   * 点选类 handler 在 setState 前调用 commit，故被改字段须显式走 patch。
   */
  const commit = useCallback((patch: Partial<CommittedSnapshot> = {}) => {
    if (status === 'executing') return // 保存中不接受新提交
    const prev = lastCommittedRef.current
    const next: CommittedSnapshot = {
      type: patch.type ?? txType,
      amountCents: patch.amountCents ?? parseAmountCents(amountYuan) ?? prev.amountCents,
      categoryId: patch.categoryId ?? categoryId,
      bizDate: patch.bizDate ?? bizDate,
      note: patch.note ?? note,
    }
    if (
      next.type === prev.type && next.amountCents === prev.amountCents &&
      next.categoryId === prev.categoryId && next.bizDate === prev.bizDate &&
      next.note === prev.note
    ) return
    const editedTx: TransactionItem = {
      ...tx,
      type: next.type,
      amountCents: next.amountCents,
      categoryId: next.categoryId || undefined,
      bizDate: next.bizDate,
      note: next.note,
    }
    lastCommittedRef.current = next
    onEditCommit({ ...proposal, transactions: [editedTx] })
  }, [status, txType, amountYuan, categoryId, bizDate, note, tx, proposal, onEditCommit])

  // ─── 分类两级结构 ───
  const primaries = categories.filter(c => c.kind === txType && !c.parentId)
  const selectedCat = categories.find(c => c.id === categoryId)
  const activeParentId = selectedCat ? (selectedCat.parentId ?? selectedCat.id) : ''
  const children = categories.filter(c => c.parentId === activeParentId)
  const displayCat = selectedCat || null

  const handleAmountBlur = useCallback(() => {
    const cents = parseAmountCents(amountYuan)
    if (cents === null) {
      setAmountError('金额必须大于 0')
      return
    }
    setAmountError(null)
    if (cents !== lastCommittedRef.current.amountCents) {
      commit({ amountCents: cents })
    }
  }, [amountYuan, commit])

  const handleNoteBlur = useCallback(() => {
    if (note !== lastCommittedRef.current.note) commit({ note })
  }, [note, commit])

  return (
    <div
      className="rounded-xl p-3 mx-4 my-2"
      style={{
        background: 'var(--glass)',
        border: '1px solid var(--glass-border)',
      }}
      data-testid="confirmation-card"
    >
      {/* 行1：图标 + 收支 + 金额 + 状态徽标 */}
      <div className="flex items-center gap-2">
        <CategoryIcon
          icon={displayCat?.icon}
          name={displayCat?.name || '💰'}
          className="text-[26px] leading-none shrink-0"
        />
        <div className="flex gap-1 shrink-0">
          {(['expense', 'income'] as const).map(t => (
            <button
              key={t}
              type="button"
              onClick={() => {
                if (t === txType) return
                setTxType(t)
                setCategoryId('') // 类型切换后需重选分类再落库
                // 不立即 commit：缺分类时等待用户点选
              }}
              className="px-2 py-1 rounded-lg text-xs font-medium transition-colors"
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
        <div className="flex items-center gap-1 flex-1 min-w-0">
          <span className="text-base shrink-0" style={{ color: 'var(--ink2)' }}>¥</span>
          <input
            type="number"
            value={amountYuan}
            onChange={e => { setAmountYuan(e.target.value); setAmountError(null) }}
            onBlur={handleAmountBlur}
            className="w-full min-w-0 bg-transparent text-right text-lg font-bold outline-none"
            style={{ color: 'var(--ink)' }}
            min="0.01"
            step="0.01"
            data-testid="card-amount"
          />
        </div>
        <span
          className="shrink-0 text-[10px] px-1.5 py-0.5 rounded-full"
          style={{
            background: status === 'error' ? 'rgba(231,76,60,0.12)' : 'rgba(255,255,255,0.4)',
            color: status === 'error' ? 'var(--expense)' : 'var(--ink3)',
            border: '1px solid var(--glass-border)',
          }}
          data-testid="card-status"
        >
          {status === 'executing' ? '保存中…' : status === 'committed' ? '✓ 已记录' : '⚠️ 保存失败'}
        </span>
      </div>
      {(amountError || status === 'error') && (
        <div className="text-xs mt-1" style={{ color: 'var(--expense)' }}>
          {amountError || '保存失败，修改任意字段将自动重试'}
        </div>
      )}

      {/* 行2：一级分类（横向滚动胶囊） */}
      <div
        className="flex gap-1.5 overflow-x-auto mt-2 pb-1"
        style={{ scrollbarWidth: 'none' }}
        data-testid="primary-cats"
      >
        {primaries.map(cat => (
          <button
            key={cat.id}
            type="button"
            onClick={() => {
              setCategoryId(cat.id)
              commit({ categoryId: cat.id })
            }}
            className="shrink-0 flex items-center gap-1 px-2 py-1 rounded-lg text-xs transition-colors"
            style={{
              background: activeParentId === cat.id ? 'var(--accent)' : 'var(--glass)',
              color: activeParentId === cat.id ? 'white' : 'var(--ink2)',
              border: '1px solid var(--glass-border)',
            }}
          >
            <CategoryIcon icon={cat.icon} name={cat.name} className="text-sm leading-none" />
            {cat.name}
          </button>
        ))}
      </div>

      {/* 行3：二级分类（有所选一级且其有子分类时出现） */}
      {children.length > 0 && (
        <div
          className="flex gap-1.5 overflow-x-auto pb-1"
          style={{ scrollbarWidth: 'none' }}
          data-testid="secondary-cats"
        >
          {children.map(child => (
            <button
              key={child.id}
              type="button"
              onClick={() => {
                setCategoryId(child.id)
                commit({ categoryId: child.id })
              }}
              className="shrink-0 px-2 py-1 rounded-lg text-xs transition-colors"
              style={{
                background: categoryId === child.id ? 'var(--accent)' : 'transparent',
                color: categoryId === child.id ? 'white' : 'var(--ink3)',
                border: '1px solid var(--glass-border)',
              }}
            >
              {child.name}
            </button>
          ))}
        </div>
      )}

      {/* 行4：日期 + 备注 同行 */}
      <div className="flex gap-2 mt-1">
        <input
          type="date"
          value={bizDate}
          onChange={e => {
            if (!e.target.value) return
            setBizDate(e.target.value)
            commit({ bizDate: e.target.value })
          }}
          className="px-2 py-1 rounded-lg text-xs outline-none shrink-0"
          style={{
            background: 'var(--glass)',
            color: 'var(--ink)',
            border: '1px solid var(--glass-border)',
          }}
          data-testid="card-date"
        />
        <input
          type="text"
          value={note}
          onChange={e => setNote(e.target.value)}
          onBlur={handleNoteBlur}
          placeholder="备注"
          className="flex-1 min-w-0 px-2 py-1 rounded-lg text-xs outline-none"
          style={{
            background: 'var(--glass)',
            color: 'var(--ink)',
            border: '1px solid var(--glass-border)',
          }}
          data-testid="card-note"
        />
      </div>

      {/* 警告（如低置信度）压缩为一行 */}
      {proposal.warnings.length > 0 && (
        <div className="mt-1 text-[10px] truncate" style={{ color: 'var(--warning-text, #856404)' }}>
          ⚠️ {proposal.warnings.join('；')}
        </div>
      )}
    </div>
  )
}
