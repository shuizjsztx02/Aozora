/**
 * T-M4-02c: 助手记账卡 — 第九轮 REQ-15/T9-7 v2 两态布局。
 *
 * 免确认模式（第八轮 Bug-6）不变：解析成功后交易已由 use-assistant 直接本地
 * 写入，任何字段改动 = 替换保存（softDelete 旧 + insert 新，由上层处理）。
 *
 * 两态（对照截图 #10 逐行）：
 * - **展示态**（默认，紧凑只读）：外层 flex 左列——行1 分类图标 + `一级 · 二级`
 *   文字；行2 收支柱子（支出橙块/收入绿块）+ 账户 chip（可换）。右侧金额大号
 *   纵跨两行居中。行3 日期 | 备注；行4 账本名 + 状态徽标 | [编辑] [撤销]。
 *   支柱/账户 chip 展示态点击即触发既有替换写。
 * - **编辑态**（点「编辑」进入）：控件全集（金额输入、一级/二级分类宫格、
 *   日期、备注、账户下拉）+ 底部「完成」收起（复用第八轮常驻编辑器逻辑）。
 *
 * 「撤销」= 直连上层 undoExecution 软删该笔（卡片存续期常驻可用，不限 5s 窗）；
 * 撤销后上层卸载本卡，消息流转既有「已撤销」灰态。
 */

import { useState, useCallback, useRef } from 'react'
import type { ActionProposal, TransactionItem } from '../../domain/action-proposal'
import type { AccountData, CategoryData } from '../../platform/bridge/bridge-types'
import { CategoryIcon } from '../../components/CategoryIcon'
import DateField from '../../components/DateField'
import type { CardStatus } from './use-assistant'

interface ConfirmationCardProps {
  proposal: ActionProposal
  categories: CategoryData[]
  /** 上层本地写入状态：executing=保存中 committed=已记录 error=保存失败（可改可重试） */
  status: CardStatus
  /** 提交编辑后的完整提案（上层执行替换写） */
  onEditCommit: (edited: ActionProposal) => void
  /** T9-7（REQ-15）：可选账户列表（展示态 chip 下拉 + 编辑态下拉） */
  accounts: AccountData[]
  /** T9-7：本笔所记账本名（行4 展示） */
  bookName: string
  /** T9-7：当前笔落库账户（= tx.accountId ?? defaultAccountId，卡片初始值） */
  selectedAccountId: string | null
  /** T9-7：撤销该笔（软删，卡片存续期任意时刻可用） */
  onUndo: () => void
}

/** 已保存值快照，避免同值重复触发替换写；展示态渲染以此为准。 */
interface CommittedSnapshot {
  type: TransactionItem['type']
  amountCents: number
  categoryId: string
  bizDate: string
  note: string
  accountId: string | null
}

export function ConfirmationCard(props: ConfirmationCardProps) {
  const tx = props.proposal.transactions?.[0]
  if (!tx) return null
  // 早期返回必须在 hooks 之前：内层组件承载 hooks
  return <ConfirmationCardBody {...props} tx={tx} />
}

function ConfirmationCardBody({
  proposal, categories, status, onEditCommit, accounts, bookName, selectedAccountId, onUndo, tx,
}: ConfirmationCardProps & { tx: TransactionItem }) {
  const initial: CommittedSnapshot = {
    type: tx.type,
    amountCents: tx.amountCents,
    categoryId: tx.categoryId || '',
    bizDate: tx.bizDate,
    note: tx.note,
    accountId: selectedAccountId ?? null,
  }
  const [editing, setEditing] = useState(false)
  const [amountYuan, setAmountYuan] = useState((tx.amountCents / 100).toFixed(2))
  const [categoryId, setCategoryId] = useState(tx.categoryId || '')
  const [bizDate, setBizDate] = useState(tx.bizDate)
  const [note, setNote] = useState(tx.note)
  const [txType, setTxType] = useState(tx.type)
  const [amountError, setAmountError] = useState<string | null>(null)
  const [accountId, setAccountId] = useState<string | null>(selectedAccountId ?? null)
  const [accountMenuOpen, setAccountMenuOpen] = useState(false)
  // committed 的 state 镜像（ref 做同值比较，state 驱动展示态响应式刷新）
  const [committed, setCommitted] = useState<CommittedSnapshot>(initial)
  const lastCommittedRef = useRef<CommittedSnapshot>(initial)

  // C-M2 fix: 字符串解析避免浮点精度问题
  const parseAmountCents = (s: string): number | null => {
    const cents = Math.round(parseFloat(s) * 100)
    if (isNaN(cents) || cents < 1) return null
    return cents
  }

  /**
   * 构建编辑后提案并（值有变化且合法时）提交。
   * 未点名的字段取**当前本地态**（如支柱已切收入后点分类，type 用新值而非旧快照）；
   * 点选类 handler 在 setState 前调用 commit，故被改字段须显式走 patch。
   * accountId 以 `!== undefined` 判定：null 是合法的显式值（不使用账户），
   * 不能走 `??` 兜底回退。
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
      accountId: patch.accountId !== undefined ? patch.accountId : accountId,
    }
    if (
      next.type === prev.type && next.amountCents === prev.amountCents &&
      next.categoryId === prev.categoryId && next.bizDate === prev.bizDate &&
      next.note === prev.note && next.accountId === prev.accountId
    ) return
    const editedTx: TransactionItem = {
      ...tx,
      type: next.type,
      amountCents: next.amountCents,
      categoryId: next.categoryId || undefined,
      bizDate: next.bizDate,
      note: next.note,
      // 键显式存在：null = 不使用账户（use-assistant 原样透传），string = 换账户
      accountId: next.accountId,
    }
    lastCommittedRef.current = next
    setCommitted(next)
    onEditCommit({ ...proposal, transactions: [editedTx] })
  }, [status, txType, amountYuan, categoryId, bizDate, note, accountId, tx, proposal, onEditCommit])

  /** 收支柱子切换；展示态点击即替换写，编辑态沿用旧语义（清空分类待重选后再写）。 */
  const handleTypeChange = (t: TransactionItem['type'], immediate: boolean) => {
    if (t === txType) return
    setTxType(t)
    setCategoryId('') // 类型切换后需重选分类再落库
    if (immediate) commit({ type: t, categoryId: '' })
  }

  const handleAccountSelect = (id: string | null) => {
    setAccountId(id)
    setAccountMenuOpen(false)
    commit({ accountId: id })
  }

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

  // ─── 分类两级结构（编辑态宫格跟随本地选择） ───
  const primaries = categories.filter(c => c.kind === txType && !c.parentId)
  const editInfo = catInfo(categoryId)
  const activeParentId = editInfo.cat ? (editInfo.cat.parentId ?? editInfo.cat.id) : ''
  const children = categories.filter(c => c.parentId === activeParentId)

  /** 分类 id →（图标分类, `一级 · 二级` 文字, 图标——二级无图标时沿用一级）。
   *  function 声明提升：编辑态派生值在其定义前使用。 */
  function catInfo(catId: string) {
    const cat = categories.find(c => c.id === catId)
    if (!cat) return { cat: null, icon: null, label: '未分类' }
    if (cat.parentId) {
      const parent = categories.find(c => c.id === cat.parentId)
      return {
        cat,
        icon: cat.icon ?? parent?.icon ?? null,
        label: parent ? `${parent.name} · ${cat.name}` : cat.name,
      }
    }
    return { cat, icon: cat.icon ?? null, label: cat.name }
  }

  const accountLabel = (id: string | null) =>
    id ? (accounts.find(a => a.id === id)?.name || '账户') : '不使用账户'

  const cardShell = 'rounded-xl p-3 mx-4 my-2'
  const cardStyle = {
    background: 'var(--glass)',
    border: '1px solid var(--glass-border)',
  } as const
  const smallBtnStyle = {
    background: 'var(--glass)',
    color: 'var(--ink2)',
    border: '1px solid var(--glass-border)',
  } as const

  const statusBadge = <StatusBadge status={status} />

  // ─────────────── 展示态（REQ-15 四行） ───────────────
  if (!editing) {
    const view = catInfo(committed.categoryId)
    const viewAccount = accounts.find(a => a.id === committed.accountId) ?? null
    return (
      <div className={cardShell} style={cardStyle} data-testid="confirmation-card">
        {/* 行1+2（左列）+ 金额（右列纵跨两行居中） */}
        <div className="flex gap-2">
          <div className="flex-1 min-w-0 flex flex-col justify-between py-0.5">
            {/* 行1：分类图标 + 一级 · 二级 */}
            <div className="flex items-center gap-1.5 min-w-0">
              <CategoryIcon
                icon={view.icon}
                name={view.cat?.name || '💰'}
                className="text-[22px] leading-none shrink-0"
              />
              <span
                className="text-sm truncate"
                style={{ color: 'var(--ink)' }}
                data-testid="card-cat-label"
              >
                {view.label}
              </span>
            </div>
            {/* 行2：收支柱子（橙/绿色块）+ 账户 chip */}
            <div className="flex items-center gap-1.5 mt-1.5 min-w-0">
              {(['expense', 'income'] as const).map(t => (
                <button
                  key={t}
                  type="button"
                  onClick={() => handleTypeChange(t, true)}
                  className="px-2 py-0.5 rounded-lg text-xs font-medium transition-colors shrink-0"
                  style={{
                    background: committed.type === t
                      ? (t === 'expense' ? 'var(--expense)' : 'var(--income)')
                      : 'transparent',
                    color: committed.type === t ? 'white' : 'var(--ink2)',
                    border: committed.type === t ? 'none' : '1px solid var(--glass-border)',
                  }}
                  data-testid={`view-type-${t}`}
                >
                  {t === 'expense' ? '支出' : '收入'}
                </button>
              ))}
              <div className="relative shrink-0 min-w-0">
                <button
                  type="button"
                  onClick={() => setAccountMenuOpen(o => !o)}
                  className="flex items-center gap-1 px-2 py-0.5 rounded-full text-xs max-w-full"
                  style={smallBtnStyle}
                  data-testid="card-account-chip"
                >
                  <CategoryIcon
                    icon={viewAccount?.icon}
                    name="账户"
                    fallback="💳"
                    className="text-xs leading-none shrink-0"
                  />
                  <span className="truncate">{accountLabel(committed.accountId)}</span>
                  <span aria-hidden style={{ opacity: 0.6 }}>▾</span>
                </button>
                {accountMenuOpen && (
                  <div
                    className="absolute z-10 mt-1 left-0 rounded-xl p-1 max-h-48 overflow-y-auto min-w-[10rem]"
                    style={cardStyle}
                    data-testid="card-account-menu"
                  >
                    <AccountOption
                      active={committed.accountId === null}
                      onClick={() => handleAccountSelect(null)}
                    >
                      <span>不使用账户</span>
                    </AccountOption>
                    {accounts.map(a => (
                      <AccountOption
                        key={a.id}
                        active={committed.accountId === a.id}
                        onClick={() => handleAccountSelect(a.id)}
                      >
                        <CategoryIcon icon={a.icon} name={a.name} fallback="💳" className="text-sm leading-none shrink-0" />
                        <span className="truncate">{a.name}</span>
                      </AccountOption>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
          {/* 右侧金额：纵跨行1+2，居中 */}
          <div
            className="self-center shrink-0 text-xl font-bold"
            style={{ color: 'var(--ink)' }}
            data-testid="card-amount-display"
          >
            ¥{(committed.amountCents / 100).toFixed(2)}
          </div>
        </div>

        {/* 行3：日期 | 备注 */}
        <div className="flex items-center justify-between gap-2 mt-2">
          <span className="text-xs shrink-0" style={{ color: 'var(--ink2)' }} data-testid="card-view-date">
            {committed.bizDate}
          </span>
          <span className="text-xs truncate" style={{ color: 'var(--ink3)' }} data-testid="card-view-note">
            {committed.note || '无备注'}
          </span>
        </div>

        {/* 行4：账本名 + 状态徽标 | 编辑 / 撤销 */}
        <div className="flex items-center justify-between gap-2 mt-1.5">
          <div className="flex items-center gap-1 min-w-0">
            <CategoryIcon
              name={bookName || '账本'}
              fallback="📒"
              className="text-sm leading-none shrink-0"
            />
            <span className="text-xs truncate" style={{ color: 'var(--ink2)' }} data-testid="card-book-name">
              {bookName || '账本'}
            </span>
            {statusBadge}
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="text-[10px] px-2 py-1 rounded-lg"
              style={smallBtnStyle}
              data-testid="card-edit"
            >
              编辑
            </button>
            <button
              type="button"
              onClick={onUndo}
              className="text-[10px] px-2 py-1 rounded-lg"
              style={smallBtnStyle}
              data-testid="card-undo"
            >
              撤销
            </button>
          </div>
        </div>

        {status === 'error' && (
          <div className="text-xs mt-1" style={{ color: 'var(--expense)' }}>
            保存失败，点「编辑」修改任意字段将自动重试
          </div>
        )}
        {proposal.warnings.length > 0 && (
          <div className="mt-1 text-[10px] truncate" style={{ color: 'var(--warning-text, #856404)' }}>
            ⚠️ {proposal.warnings.join('；')}
          </div>
        )}
      </div>
    )
  }

  // ─────────────── 编辑态（第八轮控件全集 + 账户下拉 + 完成） ───────────────
  return (
    <div className={cardShell} style={cardStyle} data-testid="confirmation-card">
      {/* 行1：图标 + 收支 + 金额 + 状态徽标 */}
      <div className="flex items-center gap-2">
        <CategoryIcon
          icon={editInfo.icon}
          name={editInfo.cat?.name || '💰'}
          className="text-[26px] leading-none shrink-0"
        />
        <div className="flex gap-1 shrink-0">
          {(['expense', 'income'] as const).map(t => (
            <button
              key={t}
              type="button"
              onClick={() => handleTypeChange(t, false)}
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
        {statusBadge}
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
        <DateField
          value={bizDate}
          onChange={v => {
            setBizDate(v)
            commit({ bizDate: v })
          }}
          testId="card-date"
          className="shrink-0"
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

      {/* 行5：账户下拉（含「不使用账户」） */}
      <div className="flex items-center gap-2 mt-1">
        <span className="text-xs shrink-0" style={{ color: 'var(--ink2)' }}>账户</span>
        <select
          value={accountId ?? ''}
          onChange={e => handleAccountSelect(e.target.value === '' ? null : e.target.value)}
          className="flex-1 min-w-0 px-2 py-1 rounded-lg text-xs outline-none"
          style={{
            background: 'var(--glass)',
            color: 'var(--ink)',
            border: '1px solid var(--glass-border)',
          }}
          data-testid="card-account"
        >
          <option value="">不使用账户</option>
          {accounts.map(a => (
            <option key={a.id} value={a.id}>{a.name}</option>
          ))}
        </select>
      </div>

      {/* 警告（如低置信度）压缩为一行 */}
      {proposal.warnings.length > 0 && (
        <div className="mt-1 text-[10px] truncate" style={{ color: 'var(--warning-text, #856404)' }}>
          ⚠️ {proposal.warnings.join('；')}
        </div>
      )}

      {/* 底部「完成」收起编辑态回展示态 */}
      <button
        type="button"
        onClick={() => setEditing(false)}
        className="w-full mt-2 py-1.5 rounded-lg text-xs font-medium text-white"
        style={{ background: 'var(--accent)' }}
        data-testid="card-done"
      >
        完成
      </button>
    </div>
  )
}

/** 展示态账户下拉行（QuickEntrySheet 下拉同构，勾选当前项）。 */
function AccountOption({
  active, onClick, children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full text-left px-3 py-2 rounded-lg text-sm flex items-center gap-1.5"
      style={{ color: active ? 'var(--accent)' : 'var(--ink2)' }}
    >
      <span className="flex items-center gap-1.5 min-w-0 flex-1">{children}</span>
      {active && <span>✓</span>}
    </button>
  )
}

function StatusBadge({ status }: { status: CardStatus }) {
  return (
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
  )
}
