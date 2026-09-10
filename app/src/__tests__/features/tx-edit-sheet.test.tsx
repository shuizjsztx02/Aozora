/**
 * TxEditSheet 组件测试（T12-10 / REQ-36 主体）。
 *
 * 覆盖（卡验收）：
 * - 预填：金额/分类/备注/账本/账户/预算 chip 全部来自交易行
 * - 保存 payload：updateTransaction 全字段 + version = 当前+1 + mutationId UUID；
 *   accountId null（不使用账户）与 includeInBudget 显式布尔语义；账本 chip = 跨账本移动
 * - 失败可见可重试：桥错误经 localizeBridgeError 中文化（含 version 冲突新映射），层不关；
 *   修正后可再存
 * - 守卫：金额 ≤0 / 切收支后未重选分类 → 不调桥
 * - 删除此笔：confirm → softDeleteTransaction + triggerSync + onSaved + onClose
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TxEditSheet } from '../../features/ledger/TxEditSheet'
import type { AccountData, BookData, CategoryData, TransactionData } from '../../platform/bridge/bridge-types'

const mockBridge = vi.hoisted(() => ({
  listBooks: vi.fn(),
  listAccounts: vi.fn(),
  updateTransaction: vi.fn(),
  softDeleteTransaction: vi.fn(),
  triggerSync: vi.fn(),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

const CATS: CategoryData[] = [
  { id: 'cat-food', kind: 'expense', name: '餐饮', parentId: null, version: 1 },
  { id: 'cat-salary', kind: 'income', name: '工资', parentId: null, version: 1 },
]

const BOOKS: BookData[] = [
  { id: 'm1', ownerId: 'o', name: '总账本', icon: '📊', version: 1, isMaster: true },
  { id: 'b1', ownerId: 'o', name: '账本一', icon: '🅰️', version: 1 },
  { id: 'b2', ownerId: 'o', name: '账本二', icon: '🅱️', version: 1 },
]

const ACCOUNTS: AccountData[] = [
  { id: 'acc-1', name: '招商储蓄卡', icon: '💳', version: 1 },
  { id: 'acc-2', name: '微信零钱', icon: '💬', version: 1 },
]

const mkTx = (over?: Partial<TransactionData>): TransactionData => ({
  id: 'tx-1',
  bookId: 'b1',
  type: 'expense',
  amountCents: 5500,
  categoryId: 'cat-food',
  accountId: 'acc-1',
  note: '午饭',
  bizDate: '2026-09-03',
  occurredAt: new Date('2026-09-03T12:00:00').getTime(),
  source: 'manual',
  includeInBudget: true,
  version: 4,
  deletedAt: null,
  ...over,
})

const onClose = vi.fn()
const onSaved = vi.fn()

const renderSheet = (tx: TransactionData = mkTx()) => {
  onClose.mockClear()
  onSaved.mockClear()
  return render(
    <TxEditSheet tx={tx} categories={CATS} onClose={onClose} onSaved={onSaved} />,
  )
}

const saveCalls = () => mockBridge.updateTransaction.mock.calls

describe('TxEditSheet', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.listBooks.mockResolvedValue(BOOKS)
    mockBridge.listAccounts.mockResolvedValue(ACCOUNTS)
    mockBridge.updateTransaction.mockResolvedValue({ id: 'tx-1', version: 5 })
    mockBridge.softDeleteTransaction.mockResolvedValue({ success: true })
    mockBridge.triggerSync.mockResolvedValue({ enqueued: true })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('预填：金额/分类/备注/账本/账户/预算 chip 均来自交易行', async () => {
    renderSheet()
    expect(screen.getByTestId('tx-edit-sheet')).toBeInTheDocument()
    expect(screen.getByTestId('tx-edit-amount')).toHaveValue(55)
    expect(screen.getByTestId('tx-edit-note')).toHaveValue('午饭')
    expect(screen.getByTestId('tx-edit-cat-toggle')).toHaveTextContent('餐饮')
    await waitFor(() => expect(screen.getByTestId('entry-book-picker')).toHaveTextContent('账本一'))
    expect(screen.getByTestId('entry-account-picker')).toHaveTextContent('招商储蓄卡')
    expect(screen.getByTestId('entry-budget-picker')).toHaveTextContent('计入预算')
    // 宫格默认收起（v3 同款）
    expect(screen.queryByTestId('category-pager')).toBeNull()
  })

  it('保存 payload：全字段 + version=当前+1 + UUID mutationId + triggerSync + 宿主回调', async () => {
    const user = userEvent.setup()
    renderSheet()
    await screen.findByTestId('entry-book-picker')
    const amount = screen.getByTestId('tx-edit-amount')
    await user.clear(amount)
    await user.type(amount, '66.66')
    await user.click(screen.getByTestId('tx-edit-save'))

    await waitFor(() => expect(mockBridge.updateTransaction).toHaveBeenCalledTimes(1))
    const [payload, mutationId] = saveCalls()[0]
    expect(payload).toEqual({
      id: 'tx-1',
      bookId: 'b1',
      type: 'expense',
      amountCents: 6666,
      categoryId: 'cat-food',
      accountId: 'acc-1',
      note: '午饭',
      bizDate: '2026-09-03',
      occurredAt: new Date('2026-09-03T12:00:00').getTime(), // 未改期 → 保留原值
      source: 'manual',
      includeInBudget: true,
      version: 5,
    })
    expect(mutationId).toMatch(/^[0-9a-f-]{36}$/i)
    expect(mockBridge.triggerSync).toHaveBeenCalledTimes(1)
    expect(onSaved).toHaveBeenCalled()
    expect(onClose).toHaveBeenCalled()
  })

  it('账户「不使用账户」= payload.accountId 显式 null；预算 chip 显式 false', async () => {
    const user = userEvent.setup()
    renderSheet()
    await screen.findByTestId('entry-account-picker')
    await user.click(screen.getByTestId('entry-account-picker'))
    await user.click(screen.getByText('不使用账户'))
    await user.click(screen.getByTestId('entry-budget-picker'))
    await user.click(screen.getByTestId('entry-budget-exclude'))
    await user.click(screen.getByTestId('tx-edit-save'))
    await waitFor(() => expect(mockBridge.updateTransaction).toHaveBeenCalled())
    expect(saveCalls()[0][0].accountId).toBe(null)
    expect(saveCalls()[0][0].includeInBudget).toBe(false)
  })

  it('账本切换 = 移动：payload.bookId 为新账本（master 不出现在列表）', async () => {
    const user = userEvent.setup()
    renderSheet()
    await screen.findByTestId('entry-book-picker')
    await user.click(screen.getByTestId('entry-book-picker'))
    const list = await screen.findByTestId('entry-book-list')
    expect(within(list).queryByText('总账本')).toBeNull()
    await user.click(within(list).getByText('账本二'))
    await user.click(screen.getByTestId('tx-edit-save'))
    await waitFor(() => expect(mockBridge.updateTransaction).toHaveBeenCalled())
    expect(saveCalls()[0][0].bookId).toBe('b2')
  })

  it('保存失败可见可重试：version 冲突映射中文、层不关，修正后再存成功', async () => {
    const user = userEvent.setup()
    renderSheet()
    await screen.findByTestId('entry-book-picker')
    mockBridge.updateTransaction.mockRejectedValueOnce(
      new Error('transaction version conflict (expected 4)'),
    )
    await user.click(screen.getByTestId('tx-edit-save'))
    const err = await screen.findByTestId('tx-edit-error')
    expect(err).toHaveTextContent('该笔记录刚被更新，请关闭编辑层后重新打开重试')
    expect(onClose).not.toHaveBeenCalled()
    expect(onSaved).not.toHaveBeenCalled()
    // 再点保存（这次成功）
    await user.click(screen.getByTestId('tx-edit-save'))
    await waitFor(() => expect(mockBridge.updateTransaction).toHaveBeenCalledTimes(2))
    expect(onClose).toHaveBeenCalled()
  })

  it('守卫：金额 ≤0 与切收支未重选分类均不调桥；重选分类后可存', async () => {
    const user = userEvent.setup()
    renderSheet()
    await screen.findByTestId('entry-book-picker')

    const amount = screen.getByTestId('tx-edit-amount')
    await user.clear(amount)
    await user.type(amount, '0')
    await user.click(screen.getByTestId('tx-edit-save'))
    expect(await screen.findByTestId('tx-edit-error')).toHaveTextContent('金额必须大于 0')
    expect(mockBridge.updateTransaction).not.toHaveBeenCalled()

    // 切收入 → 分类清空（编辑不产生缺分类笔）→ 保存被拦
    await user.clear(amount)
    await user.type(amount, '88')
    await user.click(screen.getByTestId('tx-edit-type-income'))
    await user.click(screen.getByTestId('tx-edit-save'))
    expect(await screen.findByTestId('tx-edit-error')).toHaveTextContent('请选择分类')
    expect(mockBridge.updateTransaction).not.toHaveBeenCalled()

    // 展开宫格重选「工资」后可存，type/categoryId 为新值
    await user.click(screen.getByTestId('tx-edit-cat-toggle'))
    const pager = await screen.findByTestId('category-pager')
    await user.click(within(pager).getByText('工资'))
    await user.click(screen.getByTestId('tx-edit-save'))
    await waitFor(() => expect(mockBridge.updateTransaction).toHaveBeenCalled())
    expect(saveCalls()[0][0]).toMatchObject({ type: 'income', categoryId: 'cat-salary', amountCents: 8800 })
  })

  it('删除此笔：confirm → softDelete(UUID) + triggerSync + 回调；取消则不删', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.fn().mockReturnValue(false)
    vi.stubGlobal('confirm', confirmSpy)
    renderSheet()
    await screen.findByTestId('entry-book-picker')

    await user.click(screen.getByTestId('tx-edit-delete'))
    expect(confirmSpy).toHaveBeenCalled()
    expect(mockBridge.softDeleteTransaction).not.toHaveBeenCalled()

    confirmSpy.mockReturnValue(true)
    await user.click(screen.getByTestId('tx-edit-delete'))
    await waitFor(() => expect(mockBridge.softDeleteTransaction).toHaveBeenCalledWith(
      'tx-1', expect.stringMatching(/^[0-9a-f-]{36}$/i),
    ))
    expect(mockBridge.triggerSync).toHaveBeenCalled()
    expect(onSaved).toHaveBeenCalled()
    expect(onClose).toHaveBeenCalled()
  })

  it('includeInBudget 旧数据缺省 = 计入：chip 文案「计入预算」且 payload true', async () => {
    const user = userEvent.setup()
    const tx = mkTx()
    delete (tx as { includeInBudget?: boolean }).includeInBudget
    renderSheet(tx)
    await waitFor(() => expect(screen.getByTestId('entry-budget-picker')).toHaveTextContent('计入预算'))
    await user.click(screen.getByTestId('tx-edit-save'))
    await waitFor(() => expect(mockBridge.updateTransaction).toHaveBeenCalled())
    expect(saveCalls()[0][0].includeInBudget).toBe(true)
  })
})
