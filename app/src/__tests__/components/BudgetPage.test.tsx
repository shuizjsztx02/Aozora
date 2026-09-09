/**
 * BudgetPage 组件测试（T7-6：UI-16 + 决策 #2）。
 *
 * 覆盖：
 * - 新建预算分类选择为内联宫格，不再渲染原生 <select>；首格"总预算"，收入分类不进宫格
 * - 默认"总预算"提交 → insertBudget categoryId 为 null
 * - 选中分类提交 → categoryId 为该分类
 * - 预算 bookId = pickEntryBook（第一个非 master），不会挂到总账本
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { BudgetPage } from '../../features/settings/BudgetPage'

const mockBridge = vi.hoisted(() => ({
  // T9-5b（REQ-11）：meta.get/set mock（默认无默认选择）
  getMeta: vi.fn().mockResolvedValue({}),
  setMeta: vi.fn().mockResolvedValue({ success: true }),
  call: vi.fn(),
  insertBudget: vi.fn(),
  deleteBudget: vi.fn(),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

const renderPage = () =>
  render(
    <MemoryRouter>
      <BudgetPage />
    </MemoryRouter>
  )

beforeEach(() => {
  vi.clearAllMocks()
  mockBridge.call.mockImplementation(async (cmd: string) => {
    if (cmd === 'book.list') {
      // master 排在前（对齐 BookDao ORDER BY name 的真实排序）
      return [
        { id: 'm1', ownerId: 'local', name: '总账本', icon: '📊', version: 1, isMaster: true },
        { id: 'b1', ownerId: 'local', name: '默认账本', icon: '📒', version: 1 },
      ]
    }
    if (cmd === 'category.list') {
      return [
        { id: 'c-food', kind: 'expense', name: '餐饮', parentId: null, version: 1 },
        { id: 'c-salary', kind: 'income', name: '工资', parentId: null, version: 1 },
      ]
    }
    if (cmd === 'budget.list') return []
    return []
  })
  mockBridge.insertBudget.mockResolvedValue({ id: 'bud-new' })
})

/** 打开新建预算表单并等待数据加载完成 */
async function openForm() {
  const user = userEvent.setup()
  const { container } = renderPage()
  await waitFor(() =>
    expect(screen.getByText('暂无预算，点击下方添加')).toBeInTheDocument()
  )
  await user.click(screen.getByText('＋ 添加预算'))
  return { user, container }
}

describe('BudgetPage', () => {
  it('replaces the native <select> with an inline category grid', async () => {
    const { container } = await openForm()
    // 表单打开后：不应存在原生 select
    expect(container.querySelector('select')).toBeNull()
    // 首格"总预算"；支出分类进宫格、收入分类不进
    expect(screen.getByRole('button', { name: /总预算/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /餐饮/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /工资/ })).not.toBeInTheDocument()
  })

  it('defaults to 总预算 and submits with the non-master book id', async () => {
    const { user } = await openForm()
    await user.type(screen.getByPlaceholderText('0.00'), '1000')
    await user.click(screen.getByRole('button', { name: '添加' }))

    await waitFor(() => expect(mockBridge.insertBudget).toHaveBeenCalledTimes(1))
    const [budget, mutationId] = mockBridge.insertBudget.mock.calls[0]
    expect(budget.bookId).toBe('b1') // 决策 #2：不能挂到总账本 m1
    expect(budget.categoryId).toBeNull() // 未选分类 = 总预算
    expect(mutationId).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  })

  it('submits the picked expense category id', async () => {
    const { user } = await openForm()
    await user.type(screen.getByPlaceholderText('0.00'), '500')
    await user.click(screen.getByRole('button', { name: /餐饮/ }))
    await user.click(screen.getByRole('button', { name: '添加' }))

    await waitFor(() => expect(mockBridge.insertBudget).toHaveBeenCalledTimes(1))
    const [budget] = mockBridge.insertBudget.mock.calls[0]
    expect(budget.categoryId).toBe('c-food')
    expect(budget.bookId).toBe('b1')
  })
})
