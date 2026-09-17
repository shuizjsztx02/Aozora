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
 * - **编辑态**（T12-9 / REQ-35 v3 重排，交互对齐手动记账弹框）：行1 分类图标钮
 *   （点按卡内展开/收起 CategoryPager 宫格，QuickEntry 同款）+ 收支柱子 + ¥金额
 *   + 状态徽标；行2 日期 + 备注；行3 EntryChipRow（账本/账户/预算三 chip）；
 *   警告；底部「完成」收起。卡内切账本/预算经 overrides 传入编辑保存链路
 *   （writeTransactions 用卡内 selectedBookId 覆盖 hook 层账本）。
 *
 * 「撤销」= 直连上层 undoExecution 软删该笔（卡片存续期常驻可用，不限 5s 窗）；
 * 撤销后上层卸载本卡，消息流转既有「已撤销」灰态。
 *
 * T16-2（REQ-58）：增 `readonly` 只读态——聊天历史消息的账单卡常驻（消息本身持久化，
 * 卡由 messages 重建）。只读态 = 展示态四行 + 状态徽标，隐藏「编辑」「撤销」且全部
 * 交互不可用（撤销依赖 activeTxIdsRef 内存态，切页/重启后无意义）；`undone` 整卡灰化、
 * `error` 红字、`pending-category` 不展开编辑 UI。testid 用 `history-card` 与活动卡区分。
 */

import { useState, useCallback, useRef } from 'react'
import type { ActionProposal, TransactionItem } from '../../domain/action-proposal'
import type { AccountData, BookData, CategoryData } from '../../platform/bridge/bridge-types'
import { CategoryIcon } from '../../components/CategoryIcon'
import DateField from '../../components/DateField'
import { CategoryPager, EntryChipRow } from '../ledger/entry-controls'
import type { CardStatus, ProposalWriteOverrides } from './use-assistant'

interface ConfirmationCardProps {
  proposal: ActionProposal
  categories: CategoryData[]
  /** 上层本地写入状态：executing=保存中 committed=已记录 error=保存失败（可改可重试）。
   *  T16-2：只读历史卡额外接受 `undone`（已撤销）。 */
  status: CardStatus | 'undone'
  /** 提交编辑后的完整提案（上层执行替换写）。只读态不提供。
   *  T12-9（REQ-35）：overrides = 卡内账本/预算 chip 的带外覆盖（仅显式改过时非空）。 */
  onEditCommit?: (edited: ActionProposal, overrides?: ProposalWriteOverrides) => void
  /** T9-7（REQ-15）：可选账户列表（展示态 chip 下拉 + 编辑态 EntryChipRow） */
  accounts: AccountData[]
  /** T12-9（REQ-35）：编辑态 EntryChipRow 账本列表 */
  books: BookData[]
  /** T12-9：hook 层落账本 id（chip 展示回退 + 「未切换则不覆盖」基准） */
  bookId: string | null
  /** T9-7：本笔所记账本名（行4 展示） */
  bookName: string
  /** T9-7：当前笔落库账户（= tx.accountId ?? defaultAccountId，卡片初始值） */
  selectedAccountId: string | null
  /** T9-7：撤销该笔（软删，卡片存续期任意时刻可用）。只读态不提供。 */
  onUndo?: () => void
  /** T16-2（REQ-58）：历史消息只读卡——不可编辑/撤销，无任何写路径 */
  readonly?: boolean
}

/** 已保存值快照，避免同值重复触发替换写；展示态渲染以此为准。
 *  T12-9：bookId/includeInBudget = 卡内生效值（chip 切换进快照比较，同值守卫同款）。 */
interface CommittedSnapshot {
  type: TransactionItem['type']
  amountCents: number
  categoryId: string
  bizDate: string
  note: string
  accountId: string | null
  bookId: string
  includeInBudget: boolean
}

export function ConfirmationCard(props: ConfirmationCardProps) {
  const tx = props.proposal.transactions?.[0]
  if (!tx) return null
  // 早期返回必须在 hooks 之前：内层组件承载 hooks
  return <ConfirmationCardBody {...props} tx={tx} />
}

function ConfirmationCardBody({
  proposal, categories, status, onEditCommit, accounts, books, bookId,
  bookName, selectedAccountId, onUndo, readonly = false, tx,
}: ConfirmationCardProps & { tx: TransactionItem }) {
  const initial: CommittedSnapshot = {
    type: tx.type,
    amountCents: tx.amountCents,
    categoryId: tx.categoryId || '',
    bizDate: tx.bizDate,
    note: tx.note,
    accountId: selectedAccountId ?? null,
    bookId: bookId ?? '',
    includeInBudget: true, // 聊天路径不传 → native 默认计入（REQ-17 口径）
  }
  const [editing, setEditing] = useState(!readonly && status === 'pending-category') // T12-8：待选分类态编辑 UI 预选打开（T16-2：只读卡不开）
  const [amountYuan, setAmountYuan] = useState((tx.amountCents / 100).toFixed(2))
  const [categoryId, setCategoryId] = useState(tx.categoryId || '')
  const [bizDate, setBizDate] = useState(tx.bizDate)
  const [note, setNote] = useState(tx.note)
  const [txType, setTxType] = useState(tx.type)
  const [amountError, setAmountError] = useState<string | null>(null)
  const [accountId, setAccountId] = useState<string | null>(selectedAccountId ?? null)
  const [accountMenuOpen, setAccountMenuOpen] = useState(false)
  // T12-9（REQ-35）：v3 编辑态——宫格展开态（待选分类直接展开）+ 受控页码 + chip 覆盖值
  const [catOpen, setCatOpen] = useState(!readonly && status === 'pending-category')
  const [catPage, setCatPage] = useState(0)
  const [selectedBookId, setSelectedBookId] = useState<string | null>(null)
  const [budgetChoice, setBudgetChoice] = useState<boolean | null>(null)
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
      bookId: patch.bookId ?? selectedBookId ?? bookId ?? '',
      includeInBudget: patch.includeInBudget ?? budgetChoice ?? true,
    }
    if (
      next.type === prev.type && next.amountCents === prev.amountCents &&
      next.categoryId === prev.categoryId && next.bizDate === prev.bizDate &&
      next.note === prev.note && next.accountId === prev.accountId &&
      next.bookId === prev.bookId && next.includeInBudget === prev.includeInBudget
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
    // T12-9（决策 #3 注 / REQ-35）：带外字段只在实际偏离默认时下发——
    // 账本未切换走 hook 层缺省；预算未改「不计入」走 native 缺省计入。
    const overrides: ProposalWriteOverrides = {}
    if (next.bookId && next.bookId !== (bookId ?? '')) overrides.bookId = next.bookId
    if (next.includeInBudget === false) overrides.includeInBudget = false
    lastCommittedRef.current = next
    setCommitted(next)
    onEditCommit?.(
      { ...proposal, transactions: [editedTx] },
      Object.keys(overrides).length > 0 ? overrides : undefined,
    )
  }, [
    status, txType, amountYuan, categoryId, bizDate, note, accountId,
    selectedBookId, budgetChoice, bookId, tx, proposal, onEditCommit,
  ])

  /** 收支柱子切换；展示态点击即替换写，编辑态沿用旧语义（清空分类待重选后再写）。 */
  const handleTypeChange = (t: TransactionItem['type'], immediate: boolean) => {
    if (t === txType) return
    setTxType(t)
    setCategoryId('') // 类型切换后需重选分类再落库
    setCatPage(0)     // T12-6 同款：切收支宫格页码归零
    if (immediate) commit({ type: t, categoryId: '' })
  }

  const handleAccountSelect = (id: string | null) => {
    setAccountId(id)
    setAccountMenuOpen(false)
    commit({ accountId: id })
  }

  /** T12-9：卡内切账本 = 该笔改挂新账本（替换写由上层执行）。 */
  const handleBookSelect = (id: string) => {
    setSelectedBookId(id)
    commit({ bookId: id })
  }

  const handleBudgetSelect = (v: boolean) => {
    setBudgetChoice(v)
    commit({ includeInBudget: v })
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

  // ─── 分类宫格（T12-9 v3：QuickEntry 同款平铺，含二级，过滤口径 categories.kind） ───
  const pagerCats = categories.filter(c => c.kind === txType)
  const editInfo = catInfo(categoryId)

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
    // T16-2（REQ-58）：只读历史卡「已撤销」→ 整卡灰化
    ...(readonly && status === 'undone' ? { opacity: 0.6 } : {}),
  } as const
  const smallBtnStyle = {
    background: 'var(--glass)',
    color: 'var(--ink2)',
    border: '1px solid var(--glass-border)',
  } as const

  const statusBadge = <StatusBadge status={status} readonly={readonly} />

  // ─────────────── 展示态（REQ-15 四行） ───────────────
  if (!editing) {
    const view = catInfo(committed.categoryId)
    const viewAccount = accounts.find(a => a.id === committed.accountId) ?? null
    return (
      <div
        className={cardShell}
        style={cardStyle}
        data-testid={readonly ? 'history-card' : 'confirmation-card'}
      >
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
                  onClick={readonly ? undefined : () => handleTypeChange(t, true)}
                  disabled={readonly}
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
                  onClick={readonly ? undefined : () => setAccountMenuOpen(o => !o)}
                  disabled={readonly}
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
                  {!readonly && <span aria-hidden style={{ opacity: 0.6 }}>▾</span>}
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
          {!readonly && (
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
          )}
        </div>

        {status === 'error' && (
          <div className="text-xs mt-1" style={{ color: 'var(--expense)' }}>
            {readonly ? '记账失败，该笔未写入' : '保存失败，点「编辑」修改任意字段将自动重试'}
          </div>
        )}
        {status === 'pending-category' && (
          <div className="text-xs mt-1" style={{ color: 'var(--warning-text, #856404)' }} data-testid="card-pending-hint">
            {readonly ? '未识别到分类，尚未记账' : '未识别到分类，点「编辑」选一个后保存（尚未记账）'}
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

  // ─────────────── 编辑态（T12-9 / REQ-35 v3：QuickEntry 同款交互） ───────────────
  return (
    <div className={cardShell} style={cardStyle} data-testid="confirmation-card">
      {/* 行1：分类图标钮（点击卡内展开/收起宫格）+ 收支 + 金额 + 状态徽标 */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setCatOpen(o => !o)}
          className="flex items-center gap-1 shrink-0 px-1.5 py-1 rounded-lg"
          style={{ background: catOpen ? 'var(--glass)' : 'transparent' }}
          data-testid="card-cat-toggle"
          aria-expanded={catOpen}
        >
          <CategoryIcon
            icon={editInfo.icon}
            name={editInfo.cat?.name || '💰'}
            className="text-[26px] leading-none shrink-0"
          />
          <span className="text-xs max-w-[5.5rem] truncate" style={{ color: 'var(--ink2)' }}>
            {editInfo.label}
          </span>
          <span aria-hidden style={{ opacity: 0.6 }}>▾</span>
        </button>
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
      {/* T12-8（REQ-30）：待选分类引导——选定后 use-assistant 接管首次写入 */}
      {status === 'pending-category' && !amountError && (
        <div className="text-xs mt-1" style={{ color: 'var(--warning-text, #856404)' }} data-testid="card-pending-hint">
          未识别到分类，请选一个后保存
        </div>
      )}
      {/* 卡内展开的分类宫格（T12-6 共享组件；每页 10 格 2×5，>10 类横滑翻页） */}
      {catOpen && (
        <div className="mt-2">
          <CategoryPager
            categories={pagerCats}
            selectedId={categoryId}
            onSelect={id => {
              setCategoryId(id)
              commit({ categoryId: id })
            }}
            page={catPage}
            onPageChange={setCatPage}
          />
        </div>
      )}

      {/* 行2：日期 + 备注 同行 */}
      <div className="flex gap-2 mt-2">
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

      {/* 行3：账本 / 账户 / 预算 chip 行（T12-6 共享组件，QuickEntry 同款交互） */}
      <div className="mt-2">
        <EntryChipRow
          books={books}
          accounts={accounts}
          selectedBookId={selectedBookId ?? bookId}
          selectedAccountId={accountId}
          includeInBudget={budgetChoice ?? true}
          onBookSelect={handleBookSelect}
          onAccountSelect={handleAccountSelect}
          onBudgetSelect={handleBudgetSelect}
        />
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

/** T16-2（REQ-58）：只读历史卡增 `undone`（已撤销）文案；`error` 在历史卡上口径为「记账失败」。 */
function StatusBadge({ status, readonly = false }: { status: CardStatus | 'undone'; readonly?: boolean }) {
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
      {status === 'executing' ? '保存中…'
        : status === 'undone' ? '已撤销'
        : status === 'committed' ? '✓ 已记录'
        : status === 'pending-category' ? '⚠️ 待选分类'
        : readonly ? '⚠️ 记账失败' : '⚠️ 保存失败'}
    </span>
  )
}
