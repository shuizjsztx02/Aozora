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
 *
 * T16-3（REQ-59）：编辑态改**草稿态**——字段改动只更新本地草稿，卡内**不再有任何即时写路径**
 * （原 `commit()` 已删）。底部「完成」→「确认修改」展开**新旧对照区**（旧 = 进入编辑前的
 * 已保存快照 `committed`，新 = 当前草稿 `draft`，逐字段差异高亮、未变更字段弱化）：
 * 对照区「确认」才调 `onEditCommit`（上层替换写链路零改）、「取消」弃草稿退回展示态（不写库）。
 * 展示态原有的支柱/账户 chip 快捷改同样落草稿并直接展开对照区（确认后才生效）。
 * `pending-category`（未写库首提）同走此流，确认后才触发首写。
 */

import { useState } from 'react'
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
  /** T-M7-03e：确认门上的「拒绝」（审批四值 `rejected`）——**零写**、任务不中止。
   *  只在 `pending-confirm`（agent 挂起的提案）态出现；未写库的卡上它取代「撤销」
   *  （撤销一笔还没记的账没有意义）。 */
  onReject?: () => void
  /** T-M7-03e：本轮仍在流式执行——确认门的**动作尚未启用**（`done.proposals` 才是
   *  权威清单）。按钮禁用 + 一行说明；卡本身照样可见（用户提前看见 agent 想做什么）。 */
  approvalBusy?: boolean
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
  bookName, selectedAccountId, onUndo, onReject, approvalBusy = false, readonly = false, tx,
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
  const [editing, setEditing] = useState(
    // T12-8：待选分类态编辑 UI 预选打开（T16-2：只读卡不开）
    // T-M7-02i：待确认态同理——卡内「确认」是写入的唯一入口，开在编辑态才有那个按钮
    !readonly && (status === 'pending-category' || status === 'pending-confirm'),
  )
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
  // 已保存基线（T16-3：仅「确认」时前移；展示态与对照区「旧卡」均以此为准）
  const [committed, setCommitted] = useState<CommittedSnapshot>(initial)
  // T16-3（REQ-59）：新旧对照区展开态
  const [confirming, setConfirming] = useState(false)

  /** T-M7-03e：确认门（agent 挂起的提案）——「拒绝」取代「撤销」，确认要在收尾后才可用。 */
  const isApproval = status === 'pending-confirm'

  // C-M2 fix: 字符串解析避免浮点精度问题
  const parseAmountCents = (s: string): number | null => {
    const cents = Math.round(parseFloat(s) * 100)
    if (isNaN(cents) || cents < 1) return null
    return cents
  }

  /** T16-3：当前草稿快照。金额非法时沿用已保存值（与旧 commit 的兜底同口径）。 */
  const draft: CommittedSnapshot = {
    type: txType,
    amountCents: parseAmountCents(amountYuan) ?? committed.amountCents,
    categoryId,
    bizDate,
    note,
    accountId,
    bookId: selectedBookId ?? bookId ?? '',
    includeInBudget: budgetChoice ?? true,
  }
  const diff = diffMask(draft, committed)

  /** 展示态支柱/账户 chip 快捷改：落草稿并直接展开对照区（确认后才写库）。 */
  const openCompare = () => {
    setEditing(true)
    setConfirming(true)
  }

  /** 收支柱子切换；清空分类待重选（quick = 展示态快捷改，直接进对照区）。 */
  const handleTypeChange = (t: TransactionItem['type'], quick: boolean) => {
    if (t === txType) return
    setTxType(t)
    setCategoryId('') // 类型切换后需重选分类再落库
    setCatPage(0)     // T12-6 同款：切收支宫格页码归零
    if (quick) openCompare()
  }

  const handleAccountSelect = (id: string | null, quick = false) => {
    setAccountId(id)
    setAccountMenuOpen(false)
    if (quick && id !== committed.accountId) openCompare()
  }

  /** T12-9：卡内切账本 = 该笔改挂新账本（替换写由上层执行）。 */
  const handleBookSelect = (id: string) => {
    setSelectedBookId(id)
  }

  const handleBudgetSelect = (v: boolean) => {
    setBudgetChoice(v)
  }

  const handleAmountBlur = () => {
    const cents = parseAmountCents(amountYuan)
    setAmountError(cents === null ? '金额必须大于 0' : null)
  }

  /** T16-3（REQ-59）：对照区「确认」——草稿无变更则不写；有变更才调上层替换写。
   *  T-M7-02i：`pending-confirm`（agent 挂起提案）是例外——那张卡**本来就还没落库**，
   *  「确认」本身就是写入的触发条件，所以无变更也要写（`pending-category` 靠「补上
   *  分类」制造差异，这里没有可制造的差异）。 */
  const handleConfirmEdit = () => {
    if (status === 'executing') return // 保存中不接受新提交
    const next = draft
    const isFirstWrite = status === 'pending-confirm'
    if (!sameSnapshot(next, committed) || isFirstWrite) {
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
      setCommitted(next)
      onEditCommit?.(
        { ...proposal, transactions: [editedTx] },
        Object.keys(overrides).length > 0 ? overrides : undefined,
      )
    }
    setConfirming(false)
    setEditing(false)
  }

  /** T16-3（REQ-59）：「取消」= 弃草稿回展示态，不写库（字段复位到已保存值）。 */
  const handleCancelEdit = () => {
    setTxType(committed.type)
    setAmountYuan((committed.amountCents / 100).toFixed(2))
    setCategoryId(committed.categoryId)
    setBizDate(committed.bizDate)
    setNote(committed.note)
    setAccountId(committed.accountId)
    setSelectedBookId(committed.bookId === (bookId ?? '') ? null : committed.bookId)
    setBudgetChoice(committed.includeInBudget ? null : false)
    setAmountError(null)
    setAccountMenuOpen(false)
    setCatOpen(false)
    setConfirming(false)
    setEditing(false)
  }

  // ─── 分类宫格（T12-9 v3：QuickEntry 同款平铺，含二级，过滤口径 categories.kind） ───
  const catInfo = (catId: string) => catInfoOf(categories, catId)
  const accountLabel = (id: string | null) => accountLabelOf(accounts, id)
  const pagerCats = categories.filter(c => c.kind === txType)
  const editInfo = catInfo(categoryId)

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
    const viewBookName = books.find(b => b.id === committed.bookId)?.name ?? bookName ?? '账本'
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
                      onClick={() => handleAccountSelect(null, true)}
                    >
                      <span>不使用账户</span>
                    </AccountOption>
                    {accounts.map(a => (
                      <AccountOption
                        key={a.id}
                        active={committed.accountId === a.id}
                        onClick={() => handleAccountSelect(a.id, true)}
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
              name={viewBookName}
              fallback="📒"
              className="text-sm leading-none shrink-0"
            />
            <span className="text-xs truncate" style={{ color: 'var(--ink2)' }} data-testid="card-book-name">
              {viewBookName}
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
              {/* T-M7-03e：确认门上的卡还没写库——「撤销」换成「拒绝」（同一处、同一种按钮） */}
              {isApproval && onReject ? (
                <button
                  type="button"
                  onClick={onReject}
                  className="text-[10px] px-2 py-1 rounded-lg"
                  style={smallBtnStyle}
                  data-testid="card-reject"
                >
                  拒绝
                </button>
              ) : (
                <button
                  type="button"
                  onClick={onUndo}
                  className="text-[10px] px-2 py-1 rounded-lg"
                  style={smallBtnStyle}
                  data-testid="card-undo"
                >
                  撤销
                </button>
              )}
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
        {status === 'pending-confirm' && (
          <div className="text-xs mt-1" style={{ color: 'var(--warning-text, #856404)' }} data-testid="card-pending-hint">
            {readonly ? '待确认，尚未记账'
              : approvalBusy ? '本轮仍在执行，稍候可确认（尚未记账）'
              : '点「确认」后才会记账'}
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
      {/* T-M7-02i：待确认引导——agent 挂起的提案（多步写入强制确认，T-M7-02e 决策 3）
       *  T-M7-03e：本轮还在跑时动作未启用（`done.proposals` 才是权威清单），如实说明 */}
      {status === 'pending-confirm' && !amountError && (
        <div className="text-xs mt-1" style={{ color: 'var(--warning-text, #856404)' }} data-testid="card-pending-hint">
          {approvalBusy ? '本轮仍在执行，稍候再确认这次改动（尚未记账）' : '确认无误后点「确认」记录（尚未记账）'}
        </div>
      )}
      {/* 卡内展开的分类宫格（T12-6 共享组件；每页 10 格 2×5，>10 类横滑翻页） */}
      {catOpen && (
        <div className="mt-2">
          <CategoryPager
            categories={pagerCats}
            selectedId={categoryId}
            onSelect={setCategoryId}
            page={catPage}
            onPageChange={setCatPage}
          />
        </div>
      )}

      {/* 行2：日期 + 备注 同行 */}
      <div className="flex gap-2 mt-2">
        <DateField
          value={bizDate}
          onChange={setBizDate}
          testId="card-date"
          className="shrink-0"
        />
        <input
          type="text"
          value={note}
          onChange={e => setNote(e.target.value)}
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

      {/* T16-3（REQ-59）：新旧对照区——旧快照卡 vs 新草稿卡，逐字段差异高亮 */}
      {confirming && (
        <div className="mt-2" data-testid="card-compare">
          <div className="text-[10px] mb-1" style={{ color: 'var(--ink3)' }}>新旧对照</div>
          <SnapshotCard
            testId="card-compare-old"
            snapshot={committed}
            categories={categories}
            accounts={accounts}
            books={books}
            bookName={bookName}
            diff={diff}
          />
          <div className="text-center text-[10px] my-0.5" style={{ color: 'var(--ink3)' }}>↓</div>
          <SnapshotCard
            testId="card-compare-new"
            snapshot={draft}
            categories={categories}
            accounts={accounts}
            books={books}
            bookName={bookName}
            diff={diff}
          />
          <div className="flex gap-2 mt-2">
            <button
              type="button"
              onClick={handleCancelEdit}
              className="flex-1 py-1.5 rounded-lg text-xs font-medium"
              style={smallBtnStyle}
              data-testid="card-compare-cancel"
            >
              取消
            </button>
            <button
              type="button"
              onClick={handleConfirmEdit}
              disabled={status === 'executing' || approvalBusy}
              className="flex-1 py-1.5 rounded-lg text-xs font-medium text-white"
              style={{ background: 'var(--accent)' }}
              data-testid="card-compare-confirm"
            >
              确认
            </button>
          </div>
        </div>
      )}

      {/* 底部「确认修改」展开对照区（T16-3：不再直接收起回写）
       * executing 态不禁止此按钮——对照区的「确认」按钮已禁用，避免 React 对 disabled 按钮
       * 吞掉点击事件导致对照区打不开。
       * T-M7-03e：确认门的卡并排一个「拒绝」（零写、任务不中止）；流式期间按钮不 disable，
       * 否则同上——用户点不动却不知道为什么，引导语已经写明「本轮仍在执行」。 */}
      {!confirming && (
        <div className="flex gap-2 mt-2">
          {isApproval && onReject && (
            <button
              type="button"
              onClick={onReject}
              className="flex-1 py-1.5 rounded-lg text-xs font-medium"
              style={smallBtnStyle}
              data-testid="card-reject"
            >
              拒绝
            </button>
          )}
          <button
            type="button"
            onClick={() => setConfirming(true)}
            className={`${isApproval && onReject ? 'flex-1' : 'w-full'} py-1.5 rounded-lg text-xs font-medium text-white`}
            style={{ background: 'var(--accent)' }}
            data-testid="card-done"
          >
            确认修改
          </button>
        </div>
      )}
    </div>
  )
}

/** T16-3（REQ-59）：两份快照的逐字段差异掩码（对照区高亮依据）。 */
interface DiffMask {
  cat: boolean
  type: boolean
  amount: boolean
  date: boolean
  note: boolean
  account: boolean
  book: boolean
  budget: boolean
}

function diffMask(a: CommittedSnapshot, b: CommittedSnapshot): DiffMask {
  return {
    cat: a.categoryId !== b.categoryId,
    type: a.type !== b.type,
    amount: a.amountCents !== b.amountCents,
    date: a.bizDate !== b.bizDate,
    note: a.note !== b.note,
    account: a.accountId !== b.accountId,
    book: a.bookId !== b.bookId,
    budget: a.includeInBudget !== b.includeInBudget,
  }
}

function sameSnapshot(a: CommittedSnapshot, b: CommittedSnapshot): boolean {
  const d = diffMask(a, b)
  return !Object.values(d).some(Boolean)
}

/** 分类 id →（图标分类, `一级 · 二级` 文字, 图标——二级无图标时沿用一级）。 */
function catInfoOf(categories: CategoryData[], catId: string) {
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

function accountLabelOf(accounts: AccountData[], id: string | null) {
  return id ? (accounts.find(a => a.id === id)?.name || '账户') : '不使用账户'
}

/** T16-3（REQ-59）：对照区单卡——只读渲染一份快照（展示态四行 + 预算行）。
 *  差异字段高亮、未变更字段弱化（`diff` 掩码由两份快照比对得出）。 */
function SnapshotCard({
  testId, snapshot, categories, accounts, books, bookName, diff,
}: {
  testId: string
  snapshot: CommittedSnapshot
  categories: CategoryData[]
  accounts: AccountData[]
  books: BookData[]
  bookName: string
  diff: DiffMask
}) {
  const view = catInfoOf(categories, snapshot.categoryId)
  const viewAccount = accounts.find(a => a.id === snapshot.accountId) ?? null
  const viewBook = books.find(b => b.id === snapshot.bookId)
  const viewBookName = viewBook?.name ?? bookName ?? '账本'
  const mark = (changed: boolean) => changed
    ? { background: 'rgba(255, 208, 92, 0.30)', borderRadius: 6 }
    : { opacity: 0.55 }
  const chip = {
    background: 'var(--glass)',
    border: '1px solid var(--glass-border)',
  } as const
  return (
    <div
      className="rounded-xl p-2.5"
      style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
      data-testid={testId}
    >
      <div className="flex gap-2">
        <div className="flex-1 min-w-0 flex flex-col justify-between py-0.5">
          <div className="flex items-center gap-1.5 min-w-0" style={mark(diff.cat)} data-testid={`${testId}-cat`}>
            <CategoryIcon
              icon={view.icon}
              name={view.cat?.name || '💰'}
              className="text-[20px] leading-none shrink-0"
            />
            <span className="text-sm truncate" style={{ color: 'var(--ink)' }}>{view.label}</span>
          </div>
          <div className="flex items-center gap-1.5 mt-1.5 min-w-0">
            <span className="shrink-0" style={mark(diff.type)} data-testid={`${testId}-type`}>
              <span
                className="px-2 py-0.5 rounded-lg text-xs font-medium inline-block"
                style={{
                  background: snapshot.type === 'expense' ? 'var(--expense)' : 'var(--income)',
                  color: 'white',
                }}
              >
                {snapshot.type === 'expense' ? '支出' : '收入'}
              </span>
            </span>
            <span
              className="flex items-center gap-1 px-2 py-0.5 rounded-full text-xs min-w-0"
              style={{ ...chip, ...mark(diff.account), color: 'var(--ink2)' }}
              data-testid={`${testId}-account`}
            >
              <CategoryIcon
                icon={viewAccount?.icon}
                name="账户"
                fallback="💳"
                className="text-xs leading-none shrink-0"
              />
              <span className="truncate">{accountLabelOf(accounts, snapshot.accountId)}</span>
            </span>
          </div>
        </div>
        <div
          className="self-center shrink-0 px-1 text-lg font-bold"
          style={{ color: 'var(--ink)', ...mark(diff.amount) }}
          data-testid={`${testId}-amount`}
        >
          ¥{(snapshot.amountCents / 100).toFixed(2)}
        </div>
      </div>

      <div className="flex items-center justify-between gap-2 mt-2">
        <span
          className="text-xs shrink-0 px-1 rounded"
          style={{ color: 'var(--ink2)', ...mark(diff.date) }}
          data-testid={`${testId}-date`}
        >
          {snapshot.bizDate}
        </span>
        <span
          className="text-xs truncate px-1 rounded"
          style={{ color: 'var(--ink3)', ...mark(diff.note) }}
          data-testid={`${testId}-note`}
        >
          {snapshot.note || '无备注'}
        </span>
      </div>

      <div className="flex items-center gap-1 mt-1.5 min-w-0">
        <CategoryIcon name={viewBookName} fallback="📒" className="text-sm leading-none shrink-0" />
        <span className="text-xs truncate px-1 rounded" style={{ color: 'var(--ink2)', ...mark(diff.book) }} data-testid={`${testId}-book`}>
          {viewBookName}
        </span>
        <span
          className="shrink-0 text-[10px] px-1.5 py-0.5 rounded-full"
          style={{ ...chip, color: 'var(--ink3)', ...mark(diff.budget) }}
          data-testid={`${testId}-budget`}
        >
          {snapshot.includeInBudget ? '计入预算' : '不计入预算'}
        </span>
      </div>
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
        : status === 'pending-confirm' ? '⏳ 待确认'
        : readonly ? '⚠️ 记账失败' : '⚠️ 保存失败'}
    </span>
  )
}
