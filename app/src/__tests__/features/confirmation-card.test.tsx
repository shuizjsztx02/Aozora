/**
 * 第八轮 REQ-10/T8-6: 记账卡（常驻编辑器）测试。
 *
 * 验证：
 * - 无"确认记账/取消"按钮；状态徽标（✓ 已记录 / 保存中… / 失败）
 * - 7 要素齐：分类图标 / 收支柱 / 金额 / 一级分类 / 二级分类 / 日期 / 备注
 * - 一级/二级分类点选、金额与备注失焦 → onEditCommit 替换保存
 * - 同值不重复提交；保存中不接受新提交；非法金额拦截
 */
import { describe, it, expect, vi, type Mock } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ConfirmationCard } from '../../features/assistant/ConfirmationCard'
import type { ActionProposal } from '../../domain/action-proposal'
import type { CategoryData } from '../../platform/bridge/bridge-types'

const CATS: CategoryData[] = [
  { id: 'cat-food', kind: 'expense', name: '餐饮', parentId: null, icon: '🥟', version: 1 },
  { id: 'cat-lunch', kind: 'expense', name: '午饭', parentId: 'cat-food', version: 1 },
  { id: 'cat-dinner', kind: 'expense', name: '晚饭', parentId: 'cat-food', version: 1 },
  { id: 'cat-traffic', kind: 'expense', name: '交通', parentId: null, version: 1 },
  { id: 'cat-salary', kind: 'income', name: '工资', parentId: null, version: 1 },
]

function proposal(categoryId = 'cat-lunch'): ActionProposal {
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
      note: '午饭',
    }],
  }
}

const renderCard = (over: {
  status?: 'executing' | 'committed' | 'error',
  onEditCommit?: Mock,
  categoryId?: string,
} = {}) => {
  const onEditCommit: Mock = over.onEditCommit ?? vi.fn()
  render(
    <ConfirmationCard
      proposal={proposal(over.categoryId)}
      categories={CATS}
      status={over.status ?? 'committed'}
      onEditCommit={onEditCommit}
    />,
  )
  return { onEditCommit }
}

describe('ConfirmationCard — 常驻编辑器（T8-6）', () => {
  it('无确认/取消按钮，显示已记录徽标', () => {
    renderCard()
    expect(screen.queryByText('确认记账')).toBeNull()
    expect(screen.queryByText('取消')).toBeNull()
    expect(screen.getByTestId('card-status').textContent).toContain('已记录')
  })

  it('7 要素齐：一级宫格只含顶级分类，选中子分类时二级行显示且高亮', () => {
    renderCard()
    const primary = screen.getByTestId('primary-cats')
    expect(primary.textContent).toContain('餐饮')
    expect(primary.textContent).toContain('交通')
    expect(primary.textContent).not.toContain('午饭') // 二级不进一级行
    // 选中 cat-lunch（父=餐饮）→ 餐饮高亮 + 二级行含午饭/晚饭
    const secondary = screen.getByTestId('secondary-cats')
    expect(secondary.textContent).toContain('午饭')
    expect(secondary.textContent).toContain('晚饭')
    // 分类图标来自 CategoryIcon（餐饮 icon 🥟）
    expect(screen.getByTestId('confirmation-card').textContent).toContain('🥟')
    expect(screen.getByTestId('card-date')).toHaveValue('2026-09-08')
    expect(screen.getByTestId('card-note')).toHaveValue('午饭')
  })

  it('选中顶级分类且其有子分类时也渲染二级行', () => {
    renderCard({ categoryId: 'cat-food' })
    const secondary = screen.getByTestId('secondary-cats')
    expect(secondary.textContent).toContain('午饭')
  })

  it('无子分类时不渲染二级行（交通）', () => {
    renderCard({ categoryId: 'cat-traffic' })
    expect(screen.queryByTestId('secondary-cats')).toBeNull()
  })

  it('切换收支柱 → 一级宫格换成收入分类', () => {
    renderCard()
    fireEvent.click(screen.getByText('收入'))
    expect(screen.getByTestId('primary-cats').textContent).toContain('工资')
    expect(screen.getByTestId('primary-cats').textContent).not.toContain('餐饮')
  })

  it('点一级分类 → onEditCommit 收到该 categoryId', () => {
    const { onEditCommit } = renderCard()
    fireEvent.click(screen.getByText('交通'))
    expect(onEditCommit).toHaveBeenCalledTimes(1)
    const [edited] = onEditCommit.mock.calls[0]
    expect(edited.transactions[0].categoryId).toBe('cat-traffic')
    expect(edited.transactions[0].amountCents).toBe(4500) // 其余字段沿用当前值
  })

  it('点二级分类 → onEditCommit 收到子分类 id', () => {
    const { onEditCommit } = renderCard({ categoryId: 'cat-food' })
    fireEvent.click(screen.getByText('晚饭'))
    const [edited] = onEditCommit.mock.calls[0]
    expect(edited.transactions[0].categoryId).toBe('cat-dinner')
  })

  it('同值不重复提交：再点当前高亮一级分类不触发', () => {
    const { onEditCommit } = renderCard({ categoryId: 'cat-traffic' })
    fireEvent.click(screen.getByText('交通'))
    expect(onEditCommit).not.toHaveBeenCalled()
  })

  it('金额修改失焦才提交；备注失焦提交', async () => {
    const { onEditCommit } = renderCard()
    const amount = screen.getByTestId('card-amount')
    await userEvent.clear(amount)
    await userEvent.type(amount, '88.8')
    expect(onEditCommit).not.toHaveBeenCalled() // 输入中不提交
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
    const amount = screen.getByTestId('card-amount')
    await userEvent.clear(amount)
    await userEvent.type(amount, '0')
    fireEvent.blur(amount)
    expect(onEditCommit).not.toHaveBeenCalled()
    expect(screen.getByText('金额必须大于 0')).toBeInTheDocument()
  })

  it('保存中（executing）不接受新提交', () => {
    const { onEditCommit } = renderCard({ status: 'executing' })
    fireEvent.click(screen.getByText('交通'))
    expect(onEditCommit).not.toHaveBeenCalled()
  })

  it('error 态显示失败徽标与重试提示', () => {
    renderCard({ status: 'error' })
    expect(screen.getByTestId('card-status').textContent).toContain('保存失败')
    expect(screen.getByText('保存失败，修改任意字段将自动重试')).toBeInTheDocument()
  })

  it('改日期 → 即时提交新 bizDate', () => {
    const { onEditCommit } = renderCard()
    fireEvent.change(screen.getByTestId('card-date'), { target: { value: '2026-09-05' } })
    expect(onEditCommit).toHaveBeenCalledTimes(1)
    expect(onEditCommit.mock.calls[0][0].transactions[0].bizDate).toBe('2026-09-05')
  })
})

async function waitForCommit(spy: Mock, times: number) {
  for (let i = 0; i < 50 && spy.mock.calls.length < times; i++) {
    await new Promise(r => setTimeout(r, 10))
  }
  expect(spy).toHaveBeenCalledTimes(times)
}
