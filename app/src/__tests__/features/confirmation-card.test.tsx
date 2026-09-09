/**
 * 第九轮 REQ-15/T9-7: 记账卡 v2（两态）测试。
 *
 * 验证：
 * - 展示态（默认）：REQ-15 四行要素齐（分类名/收支柱子/账户 chip/金额/日期/
 *   备注/账本名/编辑|撤销），无金额输入框与宫格
 * - 点「编辑」进入编辑态：第八轮控件全集行为不回归（宫格/金额失焦/备注/日期/
 *   同值守卫/executing 守卫/非法金额/error 徽标）+ 账户下拉 + 「完成」收起
 * - 展示态换账户/换收支柱子 → 即时替换写（含「不使用账户」= accountId null）
 * - 「撤销」按钮直连 onUndo（任意时刻可用）
 */
import { describe, it, expect, vi, type Mock } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ConfirmationCard } from '../../features/assistant/ConfirmationCard'
import type { ActionProposal } from '../../domain/action-proposal'
import type { AccountData, CategoryData } from '../../platform/bridge/bridge-types'

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
  status?: 'executing' | 'committed' | 'error',
  onEditCommit?: Mock,
  onUndo?: Mock,
  categoryId?: string,
  note?: string,
  accounts?: AccountData[],
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

  it('展示态为紧凑只读：无金额输入框 / 分类宫格 / 日期输入', () => {
    renderCard()
    expect(screen.queryByTestId('card-amount')).toBeNull()
    expect(screen.queryByTestId('primary-cats')).toBeNull()
    expect(screen.queryByTestId('secondary-cats')).toBeNull()
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

  it('点「编辑」进入编辑态（出现金额输入框与控件全集）', async () => {
    renderCard()
    await toEdit()
    expect(screen.getByTestId('card-amount')).toBeInTheDocument()
    expect(screen.getByTestId('primary-cats')).toBeInTheDocument()
    expect(screen.getByTestId('card-date')).toBeInTheDocument()
    expect(screen.getByTestId('card-note')).toBeInTheDocument()
    expect(screen.getByTestId('card-account')).toBeInTheDocument()
    expect(screen.getByTestId('card-done')).toBeInTheDocument()
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

describe('ConfirmationCard — 编辑态（第八轮控件全集不回归）', () => {
  it('无确认/取消按钮；状态徽标保留', async () => {
    renderCard()
    await toEdit()
    expect(screen.queryByText('确认记账')).toBeNull()
    expect(screen.queryByText('取消')).toBeNull()
    expect(screen.getByTestId('card-status').textContent).toContain('已记录')
  })

  it('7 要素齐：一级宫格只含顶级分类，选中子分类时二级行显示且高亮', async () => {
    renderCard()
    await toEdit()
    const primary = screen.getByTestId('primary-cats')
    expect(primary.textContent).toContain('餐饮')
    expect(primary.textContent).toContain('交通')
    expect(primary.textContent).not.toContain('午饭') // 二级不进一级行
    const secondary = screen.getByTestId('secondary-cats')
    expect(secondary.textContent).toContain('午饭')
    expect(secondary.textContent).toContain('晚饭')
    expect(screen.getByTestId('card-date')).toHaveAttribute('data-date', '2026-09-08')
    expect(screen.getByTestId('card-note')).toHaveValue('午饭')
  })

  it('选中顶级分类且其有子分类时也渲染二级行；无子分类不渲染', async () => {
    renderCard({ categoryId: 'cat-food' })
    await toEdit()
    expect(screen.getByTestId('secondary-cats').textContent).toContain('午饭')
  })

  it('无子分类时不渲染二级行（交通）', async () => {
    renderCard({ categoryId: 'cat-traffic' })
    await toEdit()
    expect(screen.queryByTestId('secondary-cats')).toBeNull()
  })

  it('切换收支柱子 → 一级宫格换成收入分类（编辑态不即时写，待重选分类）', async () => {
    const { onEditCommit } = renderCard()
    await toEdit()
    // 编辑态下「收入」仅存在支柱按钮一处（展示态支柱随编辑态挂载不存在）
    fireEvent.click(screen.getByText('收入'))
    expect(screen.getByTestId('primary-cats').textContent).toContain('工资')
    expect(screen.getByTestId('primary-cats').textContent).not.toContain('餐饮')
    expect(onEditCommit).not.toHaveBeenCalled()
  })

  it('点一级分类 → onEditCommit 收到该 categoryId', async () => {
    const { onEditCommit } = renderCard()
    await toEdit()
    fireEvent.click(screen.getByText('交通'))
    expect(onEditCommit).toHaveBeenCalledTimes(1)
    const [edited] = onEditCommit.mock.calls[0]
    expect(edited.transactions[0].categoryId).toBe('cat-traffic')
    expect(edited.transactions[0].amountCents).toBe(4500)
  })

  it('点二级分类 → onEditCommit 收到子分类 id', async () => {
    const { onEditCommit } = renderCard({ categoryId: 'cat-food' })
    await toEdit()
    fireEvent.click(screen.getByText('晚饭'))
    expect(onEditCommit.mock.calls[0][0].transactions[0].categoryId).toBe('cat-dinner')
  })

  it('同值不重复提交：再点当前高亮一级分类不触发', async () => {
    const { onEditCommit } = renderCard({ categoryId: 'cat-traffic' })
    await toEdit()
    fireEvent.click(screen.getByText('交通'))
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
    fireEvent.click(screen.getByText('交通'))
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

  it('账户下拉换账户 → 即时替换写；选「不使用账户」→ 显式 null', async () => {
    const { onEditCommit } = renderCard()
    await toEdit()
    const select = screen.getByTestId('card-account')
    expect(select).toHaveValue('acc-bank')
    fireEvent.change(select, { target: { value: 'acc-cash' } })
    expect(onEditCommit).toHaveBeenCalledTimes(1)
    expect(onEditCommit.mock.calls[0][0].transactions[0].accountId).toBe('acc-cash')

    fireEvent.change(select, { target: { value: '' } })
    expect(onEditCommit).toHaveBeenCalledTimes(2)
    expect(onEditCommit.mock.calls[1][0].transactions[0].accountId).toBeNull()
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

  it('编辑态换账户后展示态 chip 同步显示新账户', async () => {
    const { onEditCommit } = renderCard()
    await toEdit()
    fireEvent.change(screen.getByTestId('card-account'), { target: { value: 'acc-cash' } })
    expect(onEditCommit).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByTestId('card-done'))
    expect(screen.getByTestId('card-account-chip').textContent).toContain('现金')
  })
})

async function waitForCommit(spy: Mock, times: number) {
  for (let i = 0; i < 50 && spy.mock.calls.length < times; i++) {
    await new Promise(r => setTimeout(r, 10))
  }
  expect(spy).toHaveBeenCalledTimes(times)
}
