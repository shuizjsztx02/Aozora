/**
 * BudgetPage 组件测试（T7-6：UI-16 + 决策 #2；T11-1：REQ-23 期间筛选/编辑/沿用）。
 *
 * 覆盖：
 * - 新建预算分类选择为内联宫格，不再渲染原生 <select>；首格"总预算"，收入分类不进宫格
 * - 默认"总预算"提交 → insertBudget categoryId 为 null
 * - 选中分类提交 → categoryId 为该分类
 * - 预算 bookId = pickEntryBook（第一个非 master），不会挂到总账本
 * - T11-1：列表按查看期间过滤（切月只显该月 monthly）；行点击进编辑态、
 *   保存走 updateBudget 且 version+1；沿用开关写 meta budget_carryover
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
  updateBudget: vi.fn(),
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

  // ─── T11-1（REQ-23）：期间筛选 / 编辑 / 预算沿用 ───

  /** 页面默认查看期间 = 设备当前月 */
  const now = new Date()
  const CUR = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
  const PREV_D = new Date(now.getFullYear(), now.getMonth() - 1, 1)
  const PREV = `${PREV_D.getFullYear()}-${String(PREV_D.getMonth() + 1).padStart(2, '0')}`

  const row = (id: string, period: string, extra: Record<string, unknown> = {}) => ({
    id, bookId: 'b1', budgetType: 'monthly', categoryId: null,
    period, amountCents: 100000, version: 1, ...extra,
  })

  /** 用给定预算行覆盖 beforeEach 的 budget.list 空返回 */
  function withBudgets(budgets: unknown[]) {
    mockBridge.call.mockImplementation(async (cmd: string) => {
      if (cmd === 'book.list') {
        return [
          { id: 'm1', ownerId: 'local', name: '总账本', icon: '📊', version: 1, isMaster: true },
          { id: 'b1', ownerId: 'local', name: '默认账本', icon: '📒', version: 1 },
        ]
      }
      if (cmd === 'category.list') {
        return [{ id: 'c-food', kind: 'expense', name: '餐饮', parentId: null, version: 1 }]
      }
      if (cmd === 'budget.list') return budgets
      return []
    })
  }

  it('lists only the viewed period; month arrows re-filter the list', async () => {
    withBudgets([
      row('bud-cur', CUR),
      row('bud-prev', PREV, { amountCents: 200000 }),
    ])
    const user = userEvent.setup()
    renderPage()
    // 默认当月：只显 CUR 行（行内 period 文本可见）
    await waitFor(() => expect(screen.getByText(CUR)).toBeInTheDocument())
    expect(screen.queryByText(PREV)).not.toBeInTheDocument()
    // 切到上月：只显 PREV 行
    await user.click(screen.getByLabelText('上个月'))
    expect(screen.queryByText(CUR)).not.toBeInTheDocument()
    expect(screen.getByText(PREV)).toBeInTheDocument()
  })

  it('row click opens a prefilled edit form and saves via updateBudget with version+1', async () => {
    withBudgets([row('bud-e1', CUR, { amountCents: 123450, version: 3, categoryId: 'c-food' })])
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(screen.getByText('月度预算')).toBeInTheDocument())
    await user.click(screen.getByText('月度预算'))

    // 表单预填：金额、分类、标题、保存按钮
    await waitFor(() => expect(screen.getByText('编辑预算')).toBeInTheDocument())
    const amountInput = screen.getByPlaceholderText('0.00') as HTMLInputElement
    expect(amountInput.value).toBe('1234.50')
    await user.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(mockBridge.updateBudget).toHaveBeenCalledTimes(1))
    const [budget, mutationId] = mockBridge.updateBudget.mock.calls[0]
    expect(budget.id).toBe('bud-e1')
    expect(budget.version).toBe(4) // 原 version 3 + 1
    expect(budget.amountCents).toBe(123450)
    expect(budget.categoryId).toBe('c-food')
    expect(budget.budgetType).toBe('monthly')
    expect(budget.period).toBe(CUR) // 决策 #3：period 固定为当前查看期间
    expect(mutationId).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  })

  it('new budget form stamps the viewed period, not the device month', async () => {
    withBudgets([])
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(screen.getByText('暂无预算，点击下方添加')).toBeInTheDocument())
    await user.click(screen.getByLabelText('上个月')) // 查看期间 → 上月
    await user.click(screen.getByText('＋ 添加预算'))
    await user.type(screen.getByPlaceholderText('0.00'), '300')
    await user.click(screen.getByRole('button', { name: '添加' }))

    await waitFor(() => expect(mockBridge.insertBudget).toHaveBeenCalledTimes(1))
    const [budget] = mockBridge.insertBudget.mock.calls[0]
    expect(budget.period).toBe(PREV) // 决策 #3 修复点：period 取当前查看期间
  })

  it('carryover switch toggles meta budget_carryover', async () => {
    withBudgets([])
    mockBridge.getMeta.mockResolvedValueOnce({ budget_carryover: '0' })
    const user = userEvent.setup()
    renderPage()
    const sw = await screen.findByTestId('budget-carryover-switch')
    expect(sw).toHaveAttribute('aria-checked', 'false')
    await user.click(sw)
    await waitFor(() =>
      expect(mockBridge.setMeta).toHaveBeenCalledWith('budget_carryover', '1')
    )
    expect(sw).toHaveAttribute('aria-checked', 'true')
  })
})
