/**
 * 第九轮 REQ-15/T9-7 v2 两态 + 第十二轮 T12-9（REQ-35）编辑态 v3 重排测试。
 *
 * 验证：
 * - 展示态（默认）：REQ-15 四行要素齐（分类名/收支柱子/账户 chip/金额/日期/
 *   备注/账本名/编辑|撤销），无金额输入框与宫格
 * - 编辑态 v3（T12-9）：行1 分类图标钮（card-cat-toggle 卡内展开/收起 CategoryPager）
 *   + 收支 + 金额；行2 日期+备注；行3 EntryChipRow（账本/账户/预算）；无旧
 *   primary-cats/secondary-cats 胶囊行与 card-account select
 * - 宫格点选/同值守卫/executing 守卫/金额失焦/备注/日期 行为不回归
 * - 卡内切账本 → onEditCommit 携带 {bookId} override；预算「不计入」→
 *   {includeInBudget:false}；改回默认 → 无 override
 * - 展示态换账户/换收支柱子 → 即时替换写（含「不使用账户」= accountId null）
 * - 「撤销」按钮直连 onUndo（任意时刻可用）
 * - 待选分类态（T12-8）：编辑 UI 预选打开且宫格直接展开
 * - T16-2（REQ-58）只读历史卡：四行要素齐但可写路径全无（无编辑/撤销、支柱与账户
 *   chip 禁用、不展开菜单）、undone 灰化+「已撤销」、error「记账失败」、
 *   pending-category 不展开编辑 UI
 */
import { describe, it, expect, vi, type Mock } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ConfirmationCard } from '../../features/assistant/ConfirmationCard'
import type { ActionProposal } from '../../domain/action-proposal'
import type { AccountData, BookData, CategoryData } from '../../platform/bridge/bridge-types'

const CATS: CategoryData[] = [
  { id: 'cat-food', kind: 'expense', name: '餐饮', parentId: null, icon: '🥟', version: 1 },
  { id: 'cat-lunch', kind: 'expense', name: '午饭', parentId: 'cat-food', version: 1 },
  { id: 'cat-dinner', kind: 'expense', name: '晚饭', parentId: 'cat-food', version: 1 },
  { id: 'cat-traffic', kind: 'expense', name: '交通', parentId: null, version: 1 },
  { id: 'cat-salary', kind: 'income', name: '工资', parentId: null, version: 1 },
]

const ACCOUNTS: AccountData[] = [
  { id: 'acc-bank', name: '招商银行卡', icon: '💳', version: 1 },
  { id: 'acc-cash', name: '现金', icon: '', version: 1 },
]

const BOOKS: BookData[] = [
  { id: 'book-1', ownerId: 'o', name: '默认账本', icon: '', version: 1 },
  { id: 'book-2', ownerId: 'o', name: '旅行账本', icon: '', version: 1 },
]

function proposal(categoryId = 'cat-lunch', note = '午饭'): ActionProposal {
  return {
    proposalId: 'prop-1',
    action: 'add_tx',
    confidence: 0.9,
    requiresConfirmation: false,
    message: '已记录',
    warnings: [],
    transactions: [{
      type: 'expense',
      amountCents: 4500,
      categoryId,
      bizDate: '2026-09-08',
      note,
    }],
  }
}

const renderCard = (over: {
  status?: 'executing' | 'committed' | 'error' | 'pending-category',
  onEditCommit?: Mock,
  onUndo?: Mock,
  categoryId?: string,
  note?: string,
  accounts?: AccountData[],
  books?: BookData[],
  bookId?: string | null,
  bookName?: string,
  selectedAccountId?: string | null,
} = {}) => {
  const onEditCommit: Mock = over.onEditCommit ?? vi.fn()
  const onUndo: Mock = over.onUndo ?? vi.fn()
  render(
    <ConfirmationCard
      proposal={proposal(over.categoryId, over.note)}
      categories={CATS}
      status={over.status ?? 'committed'}
      onEditCommit={onEditCommit}
      accounts={over.accounts ?? ACCOUNTS}
      books={over.books ?? BOOKS}
      bookId={over.bookId ?? 'book-1'}
      bookName={over.bookName ?? '默认账本'}
      selectedAccountId={over.selectedAccountId ?? 'acc-bank'}
      onUndo={onUndo}
    />,
  )
  return { onEditCommit, onUndo }
}

/** 点「编辑」进入编辑态。 */
const toEdit = async () => {
  fireEvent.click(screen.getByTestId('card-edit'))
}

/** 编辑态点分类图标钮展开宫格（返回后 category-pager 已挂载）。 */
const openPager = async () => {
  fireEvent.click(screen.getByTestId('card-cat-toggle'))
}

describe('ConfirmationCard — 展示态（REQ-15 行1-4）', () => {
  it('四行要素齐：分类名 `一级 · 二级` / 收支柱子 / 账户 chip / 金额 / 日期 / 备注 / 账本名', () => {
    renderCard()
    const card = screen.getByTestId('confirmation-card')
    // 行1：一级 · 二级（选中 cat-lunch，父=cat-food）
    expect(screen.getByTestId('card-cat-label').textContent).toBe('餐饮 · 午饭')
    // 行2：收支柱子（支出橙块/收入绿块）+ 账户 chip（当前账户名）
    expect(screen.getByTestId('view-type-expense').textContent).toContain('支出')
    expect(screen.getByTestId('view-type-income').textContent).toContain('收入')
    expect(screen.getByTestId('card-account-chip').textContent).toContain('招商银行卡')
    // 金额（纵跨行1+2 右侧）
    expect(screen.getByTestId('card-amount-display').textContent).toContain('45.00')
    // 行3：日期 | 备注
    expect(screen.getByTestId('card-view-date').textContent).toBe('2026-09-08')
    expect(screen.getByTestId('card-view-note').textContent).toBe('午饭')
    // 行4：账本名 + 状态徽标 + 编辑/撤销
    expect(screen.getByTestId('card-book-name').textContent).toBe('默认账本')
    expect(screen.getByTestId('card-status').textContent).toContain('已记录')
    expect(screen.getByTestId('card-edit')).toBeInTheDocument()
    expect(screen.getByTestId('card-undo')).toBeInTheDocument()
    // 分类图标（CategoryIcon）
    expect(card.textContent).toContain('🥟')
  })

  it('展示态为紧凑只读：无金额输入框 / 宫格 / chip 行 / 日期输入', () => {
    renderCard()
    expect(screen.queryByTestId('card-amount')).toBeNull()
    expect(screen.queryByTestId('card-cat-toggle')).toBeNull()
    expect(screen.queryByTestId('category-pager')).toBeNull()
    expect(screen.queryByTestId('entry-book-picker')).toBeNull()
    expect(screen.queryByTestId('card-date')).toBeNull()
    expect(screen.queryByTestId('card-note')).toBeNull()
  })

  it('未匹配分类显示占位文字；备注为空显示「无备注」', () => {
    renderCard({ categoryId: 'no-such-cat', note: '' })
    expect(screen.getByTestId('card-cat-label').textContent).toBe('未分类')
    expect(screen.getByTestId('card-view-note').textContent).toBe('无备注')
  })

  it('「撤销」按钮直连 onUndo', () => {
    const { onUndo } = renderCard()
    fireEvent.click(screen.getByTestId('card-undo'))
    expect(onUndo).toHaveBeenCalledTimes(1)
  })

  it('展示态点收支柱子「收入」→ 即时替换写（type 更新 + 分类清空）', () => {
    const { onEditCommit } = renderCard()
    fireEvent.click(screen.getByTestId('view-type-income'))
    expect(onEditCommit).toHaveBeenCalledTimes(1)
    const [edited] = onEditCommit.mock.calls[0]
    expect(edited.transactions[0].type).toBe('income')
    expect(edited.transactions[0].categoryId).toBeUndefined() // 类型切换后待重选
    expect(edited.transactions[0].amountCents).toBe(4500) // 其余字段沿用
  })

  it('展示态点当前支柱不触发替换写', () => {
    const { onEditCommit } = renderCard()
    fireEvent.click(screen.getByTestId('view-type-expense'))
    expect(onEditCommit).not.toHaveBeenCalled()
  })

  it('展示态账户 chip → 下拉列出「不使用账户」+ 账户；换账户即替换写', () => {
    const { onEditCommit } = renderCard()
    fireEvent.click(screen.getByTestId('card-account-chip'))
    const menu = screen.getByTestId('card-account-menu')
    expect(menu.textContent).toContain('不使用账户')
    expect(menu.textContent).toContain('现金')
    fireEvent.click(within(menu).getByText('现金'))
    expect(onEditCommit).toHaveBeenCalledTimes(1)
    expect(onEditCommit.mock.calls[0][0].transactions[0].accountId).toBe('acc-cash')
    // 下拉收起
    expect(screen.queryByTestId('card-account-menu')).toBeNull()
  })

  it('展示态选「不使用账户」→ 替换写 accountId 为显式 null', () => {
    const { onEditCommit } = renderCard()
    fireEvent.click(screen.getByTestId('card-account-chip'))
    fireEvent.click(within(screen.getByTestId('card-account-menu')).getByText('不使用账户'))
    expect(onEditCommit).toHaveBeenCalledTimes(1)
    expect(onEditCommit.mock.calls[0][0].transactions[0].accountId).toBeNull()
  })

  it('展示态重复选当前账户 → 同值守卫不触发', () => {
    const { onEditCommit } = renderCard()
    fireEvent.click(screen.getByTestId('card-account-chip'))
    fireEvent.click(within(screen.getByTestId('card-account-menu')).getByText('招商银行卡'))
    expect(onEditCommit).not.toHaveBeenCalled()
  })

  it('executing 态展示态点支柱不提交', () => {
    const { onEditCommit } = renderCard({ status: 'executing' })
    fireEvent.click(screen.getByTestId('view-type-income'))
    expect(onEditCommit).not.toHaveBeenCalled()
  })

  it('error 态徽标 + 引导点编辑重试', () => {
    renderCard({ status: 'error' })
    expect(screen.getByTestId('card-status').textContent).toContain('保存失败')
    expect(screen.getByText('保存失败，点「编辑」修改任意字段将自动重试')).toBeInTheDocument()
  })
})

describe('ConfirmationCard — 编辑态 v3（T12-9 / REQ-35 重排）', () => {
  it('结构：图标钮+支柱+金额 行1、日期+备注 行2、EntryChipRow 行3、完成；旧胶囊/select 全移除', async () => {
    renderCard()
    await toEdit()
    expect(screen.getByTestId('card-amount')).toBeInTheDocument()
    expect(screen.getByTestId('card-cat-toggle')).toBeInTheDocument()
    expect(screen.getByTestId('card-date')).toBeInTheDocument()
    expect(screen.getByTestId('card-note')).toBeInTheDocument()
    expect(screen.getByTestId('entry-book-picker')).toBeInTheDocument()
    expect(screen.getByTestId('entry-account-picker')).toBeInTheDocument()
    expect(screen.getByTestId('entry-budget-picker')).toBeInTheDocument()
    expect(screen.getByTestId('card-done')).toBeInTheDocument()
    // v3 删除项：两行胶囊 + 账户 select
    expect(screen.queryByTestId('primary-cats')).toBeNull()
    expect(screen.queryByTestId('secondary-cats')).toBeNull()
    expect(screen.queryByTestId('card-account')).toBeNull()
    // 宫格默认收起（点图标钮才展开）
    expect(screen.queryByTestId('category-pager')).toBeNull()
    // 无确认/取消按钮；状态徽标保留
    expect(screen.queryByText('确认记账')).toBeNull()
    expect(screen.getByTestId('card-status').textContent).toContain('已记录')
  })

  it('card-cat-toggle 展开/收起宫格；展开后平铺含二级分类（QuickEntry 同款）', async () => {
    renderCard()
    await toEdit()
    await openPager()
    const pager = screen.getByTestId('category-pager')
    expect(pager.textContent).toContain('餐饮')
    expect(pager.textContent).toContain('午饭') // 平铺：二级进宫格
    expect(pager.textContent).toContain('晚饭')
    expect(pager.textContent).toContain('交通')
    expect(pager.textContent).not.toContain('工资') // 只含当前 kind
    fireEvent.click(screen.getByTestId('card-cat-toggle'))
    expect(screen.queryByTestId('category-pager')).toBeNull()
  })

  it('宫格点分类 → onEditCommit 收到该 categoryId（无 chip 改动则无 overrides）', async () => {
    const { onEditCommit } = renderCard()
    await toEdit()
    await openPager()
    fireEvent.click(within(screen.getByTestId('category-pager')).getByText('交通'))
    expect(onEditCommit).toHaveBeenCalledTimes(1)
    const [edited, overrides] = onEditCommit.mock.calls[0]
    expect(edited.transactions[0].categoryId).toBe('cat-traffic')
    expect(edited.transactions[0].amountCents).toBe(4500)
    expect(overrides).toBeUndefined()
  })

  it('宫格点二级分类 → 直接落子分类 id', async () => {
    const { onEditCommit } = renderCard({ categoryId: 'cat-food' })
    await toEdit()
    await openPager()
    fireEvent.click(within(screen.getByTestId('category-pager')).getByText('晚饭'))
    expect(onEditCommit.mock.calls[0][0].transactions[0].categoryId).toBe('cat-dinner')
  })

  it('同值守卫：再点当前选中分类不触发', async () => {
    const { onEditCommit } = renderCard({ categoryId: 'cat-traffic' })
    await toEdit()
    await openPager()
    fireEvent.click(within(screen.getByTestId('category-pager')).getByText('交通'))
    expect(onEditCommit).not.toHaveBeenCalled()
  })

  it('编辑态切收支柱子 → 宫格换收入分类且页码归零；不即时写', async () => {
    const { onEditCommit } = renderCard()
    await toEdit()
    await openPager()
    fireEvent.click(screen.getByText('收入'))
    const pager = screen.getByTestId('category-pager')
    expect(pager.textContent).toContain('工资')
    expect(pager.textContent).not.toContain('餐饮')
    expect(onEditCommit).not.toHaveBeenCalled()
  })

  it('金额修改失焦才提交；备注失焦提交', async () => {
    const { onEditCommit } = renderCard()
    await toEdit()
    const amount = screen.getByTestId('card-amount')
    await userEvent.clear(amount)
    await userEvent.type(amount, '88.8')
    expect(onEditCommit).not.toHaveBeenCalled()
    fireEvent.blur(amount)
    await waitForCommit(onEditCommit, 1)
    expect(onEditCommit.mock.calls[0][0].transactions[0].amountCents).toBe(8880)

    const note = screen.getByTestId('card-note')
    await userEvent.clear(note)
    await userEvent.type(note, '食堂')
    expect(onEditCommit).toHaveBeenCalledTimes(1)
    fireEvent.blur(note)
    await waitForCommit(onEditCommit, 2)
    expect(onEditCommit.mock.calls[1][0].transactions[0].note).toBe('食堂')
  })

  it('非法金额 → 行内报错且不提交', async () => {
    const { onEditCommit } = renderCard()
    await toEdit()
    const amount = screen.getByTestId('card-amount')
    await userEvent.clear(amount)
    await userEvent.type(amount, '0')
    fireEvent.blur(amount)
    expect(onEditCommit).not.toHaveBeenCalled()
    expect(screen.getByText('金额必须大于 0')).toBeInTheDocument()
  })

  it('保存中（executing）编辑态不接受新提交', async () => {
    const { onEditCommit } = renderCard({ status: 'executing' })
    await toEdit()
    await openPager()
    fireEvent.click(within(screen.getByTestId('category-pager')).getByText('交通'))
    expect(onEditCommit).not.toHaveBeenCalled()
  })

  it('error 态编辑态显示失败徽标与重试提示', async () => {
    renderCard({ status: 'error' })
    await toEdit()
    expect(screen.getByTestId('card-status').textContent).toContain('保存失败')
    expect(screen.getByText('保存失败，修改任意字段将自动重试')).toBeInTheDocument()
  })

  it('改日期 → 即时提交新 bizDate', async () => {
    const { onEditCommit } = renderCard()
    await toEdit()
    fireEvent.click(screen.getByTestId('card-date'))
    fireEvent.click(screen.getByTestId('date-cell-2026-09-05'))
    expect(onEditCommit).toHaveBeenCalledTimes(1)
    expect(onEditCommit.mock.calls[0][0].transactions[0].bizDate).toBe('2026-09-05')
  })

  it('chip 换账户 → 即时替换写；「不使用账户」→ 显式 null', async () => {
    const { onEditCommit } = renderCard()
    await toEdit()
    fireEvent.click(screen.getByTestId('entry-account-picker'))
    fireEvent.click(within(screen.getByTestId('entry-account-list')).getByText('现金'))
    expect(onEditCommit).toHaveBeenCalledTimes(1)
    expect(onEditCommit.mock.calls[0][0].transactions[0].accountId).toBe('acc-cash')
    // 下拉随选择收起
    expect(screen.queryByTestId('entry-account-list')).toBeNull()

    fireEvent.click(screen.getByTestId('entry-account-picker'))
    fireEvent.click(within(screen.getByTestId('entry-account-list')).getByText('不使用账户'))
    expect(onEditCommit).toHaveBeenCalledTimes(2)
    expect(onEditCommit.mock.calls[1][0].transactions[0].accountId).toBeNull()
  })

  it('chip 切账本 → overrides 携带新 bookId；重复选当前账本不触发', async () => {
    const { onEditCommit } = renderCard()
    await toEdit()
    fireEvent.click(screen.getByTestId('entry-book-picker'))
    const list = screen.getByTestId('entry-book-list')
    fireEvent.click(within(list).getByText('旅行账本'))
    expect(onEditCommit).toHaveBeenCalledTimes(1)
    const [edited, overrides] = onEditCommit.mock.calls[0]
    expect(overrides).toEqual({ bookId: 'book-2' })
    expect(edited.transactions[0].amountCents).toBe(4500) // 其余字段沿用

    // 再开下拉重复点当前所选 → 同值守卫
    fireEvent.click(screen.getByTestId('entry-book-picker'))
    fireEvent.click(within(screen.getByTestId('entry-book-list')).getByText('旅行账本'))
    expect(onEditCommit).toHaveBeenCalledTimes(1)
  })

  it('chip 预算：不计入 → {includeInBudget:false}；改回计入 → 无 override（走 native 缺省）', async () => {
    const { onEditCommit } = renderCard()
    await toEdit()
    fireEvent.click(screen.getByTestId('entry-budget-picker'))
    fireEvent.click(screen.getByTestId('entry-budget-exclude'))
    expect(onEditCommit).toHaveBeenCalledTimes(1)
    expect(onEditCommit.mock.calls[0][1]).toEqual({ includeInBudget: false })

    fireEvent.click(screen.getByTestId('entry-budget-picker'))
    fireEvent.click(screen.getByTestId('entry-budget-include'))
    expect(onEditCommit).toHaveBeenCalledTimes(2)
    expect(onEditCommit.mock.calls[1][1]).toBeUndefined()
  })

  it('「完成」收起回展示态，编辑中的金额已随失焦落定', async () => {
    const { onEditCommit } = renderCard()
    await toEdit()
    fireEvent.click(screen.getByTestId('card-done'))
    expect(screen.queryByTestId('card-amount')).toBeNull()
    expect(screen.getByTestId('card-edit')).toBeInTheDocument()
    expect(screen.getByTestId('card-undo')).toBeInTheDocument()
    expect(onEditCommit).not.toHaveBeenCalled()
  })

  it('编辑态 chip 换账户后展示态 chip 同步显示新账户', async () => {
    const { onEditCommit } = renderCard()
    await toEdit()
    fireEvent.click(screen.getByTestId('entry-account-picker'))
    fireEvent.click(within(screen.getByTestId('entry-account-list')).getByText('现金'))
    expect(onEditCommit).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByTestId('card-done'))
    expect(screen.getByTestId('card-account-chip').textContent).toContain('现金')
  })
})

describe('ConfirmationCard — 只读历史卡（T16-2 / REQ-58）', () => {
  const renderReadonly = (over: {
    status?: 'committed' | 'undone' | 'error' | 'pending-category',
    categoryId?: string,
  } = {}) => {
    render(
      <ConfirmationCard
        readonly
        proposal={proposal(over.categoryId)}
        categories={CATS}
        status={over.status ?? 'committed'}
        accounts={ACCOUNTS}
        books={BOOKS}
        bookId="book-1"
        bookName="默认账本"
        selectedAccountId="acc-bank"
      />,
    )
    return screen.getByTestId('history-card')
  }

  it('四行要素齐（复用展示态）+ testid=history-card + 无编辑/撤销/编辑 UI', () => {
    const card = renderReadonly()
    expect(screen.getByTestId('card-cat-label').textContent).toBe('餐饮 · 午饭')
    expect(screen.getByTestId('card-account-chip').textContent).toContain('招商银行卡')
    expect(screen.getByTestId('card-amount-display').textContent).toContain('45.00')
    expect(screen.getByTestId('card-view-date').textContent).toBe('2026-09-08')
    expect(screen.getByTestId('card-view-note').textContent).toBe('午饭')
    expect(screen.getByTestId('card-book-name').textContent).toBe('默认账本')
    expect(screen.getByTestId('card-status').textContent).toContain('已记录')
    expect(card.textContent).toContain('🥟')
    // 可写路径全无：编辑/撤销钮 + 编辑态 UI + 活动卡 testid
    expect(screen.queryByTestId('card-edit')).toBeNull()
    expect(screen.queryByTestId('card-undo')).toBeNull()
    expect(screen.queryByTestId('card-amount')).toBeNull()
    expect(screen.queryByTestId('card-cat-toggle')).toBeNull()
    expect(screen.queryByTestId('confirmation-card')).toBeNull()
  })

  it('只读态收支柱子/账户 chip 不可交互（禁用 + 下拉不展开）', () => {
    renderReadonly()
    expect(screen.getByTestId('view-type-expense')).toBeDisabled()
    expect(screen.getByTestId('view-type-income')).toBeDisabled()
    expect(screen.getByTestId('card-account-chip')).toBeDisabled()
    fireEvent.click(screen.getByTestId('view-type-income'))
    fireEvent.click(screen.getByTestId('card-account-chip'))
    expect(screen.queryByTestId('card-account-menu')).toBeNull()
  })

  it('undone：徽标「已撤销」+ 整卡灰化', () => {
    const card = renderReadonly({ status: 'undone' })
    expect(screen.getByTestId('card-status').textContent).toBe('已撤销')
    expect(card.style.opacity).toBe('0.6')
  })

  it('error：徽标与红字均为「记账失败」口径（无「点编辑重试」引导）', () => {
    renderReadonly({ status: 'error' })
    expect(screen.getByTestId('card-status').textContent).toContain('记账失败')
    expect(screen.getByText('记账失败，该笔未写入')).toBeInTheDocument()
    expect(screen.queryByText(/点「编辑」/)).toBeNull()
  })

  it('pending-category：只读不展开编辑 UI，提示不含「点编辑」', () => {
    renderReadonly({ status: 'pending-category', categoryId: '' })
    expect(screen.queryByTestId('category-pager')).toBeNull()
    expect(screen.queryByTestId('card-cat-toggle')).toBeNull()
    expect(screen.getByTestId('card-cat-label').textContent).toBe('未分类')
    expect(screen.getByTestId('card-pending-hint').textContent).toBe('未识别到分类，尚未记账')
    expect(screen.getByTestId('card-status').textContent).toContain('待选分类')
  })
})

describe('ConfirmationCard — 待选分类态（T12-8 + T12-9 v3 融合）', () => {
  const pending = () => renderCard({ status: 'pending-category', categoryId: '' })

  it('编辑 UI 预选打开且宫格直接展开 + 引导文案 + 徽标「待选分类」', () => {
    pending()
    expect(screen.getByTestId('card-cat-toggle')).toBeInTheDocument()
    expect(screen.getByTestId('category-pager')).toBeInTheDocument()
    expect(screen.getByTestId('card-pending-hint').textContent).toContain('未识别到分类，请选一个后保存')
    expect(screen.getByTestId('card-status').textContent).toBe('⚠️ 待选分类')
  })

  it('宫格点分类 → onEditCommit 携带新 categoryId（上层接管首次写入）', () => {
    const { onEditCommit } = pending()
    fireEvent.click(within(screen.getByTestId('category-pager')).getByText('交通'))
    expect(onEditCommit).toHaveBeenCalledTimes(1)
    expect(onEditCommit.mock.calls[0][0].transactions[0].categoryId).toBe('cat-traffic')
  })

  it('未选分类点「完成」→ 展示态保留徽标与「尚未记账」提示', () => {
    pending()
    fireEvent.click(screen.getByTestId('card-done'))
    expect(screen.getByTestId('card-edit')).toBeInTheDocument()
    expect(screen.getByTestId('card-cat-label').textContent).toBe('未分类')
    expect(screen.getByTestId('card-pending-hint').textContent).toContain('尚未记账')
    expect(screen.getByTestId('card-status').textContent).toBe('⚠️ 待选分类')
  })
})

async function waitForCommit(spy: Mock, times: number) {
  for (let i = 0; i < 50 && spy.mock.calls.length < times; i++) {
    await new Promise(r => setTimeout(r, 10))
  }
  expect(spy).toHaveBeenCalledTimes(times)
}
