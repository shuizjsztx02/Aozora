/**
 * SavingsPage 组件测试（T9-4 / REQ-14 存款时间）。
 *
 * 覆盖：
 * - 填数值 + 周 chip（默认）→ insertSavingsGoal payload periodValue/periodUnit
 * - 不填 → null/null（不设期限）
 * - 月 chip 选中 → periodUnit 'month'
 * - 两 chip 皆取消 → 即使填了数值也不设期限（决策 #8）
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { SavingsPage } from '../../features/settings/SavingsPage'

const mockBridge = vi.hoisted(() => ({
  call: vi.fn(),
  listSavingsGoals: vi.fn(),
  insertSavingsGoal: vi.fn(),
  updateSavingsGoal: vi.fn(),
  archiveSavingsGoal: vi.fn(),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

const renderPage = () =>
  render(
    <MemoryRouter>
      <SavingsPage />
    </MemoryRouter>,
  )

const openFormAndFillBasics = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(await screen.findByText('＋ 新建目标'))
  await user.type(screen.getByTestId('savings-new-name'), '新手机')
  await user.type(screen.getByTestId('savings-new-target'), '6000')
}

const lastInsertPayload = () => mockBridge.insertSavingsGoal.mock.calls[0][0]

describe('SavingsPage · 存款时间（REQ-14）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.listSavingsGoals.mockResolvedValue([])
    mockBridge.insertSavingsGoal.mockResolvedValue({ id: 'g-new' })
  })

  it('sends periodValue/periodUnit when a number is entered (default 周)', async () => {
    const user = userEvent.setup()
    renderPage()
    await openFormAndFillBasics(user)
    await user.type(screen.getByTestId('savings-new-period'), '3')
    await user.click(screen.getByTestId('savings-add-confirm'))

    expect(mockBridge.insertSavingsGoal).toHaveBeenCalledTimes(1)
    expect(lastInsertPayload()).toMatchObject({ periodValue: 3, periodUnit: 'week' })
  })

  it('sends null/null when period is left empty', async () => {
    const user = userEvent.setup()
    renderPage()
    await openFormAndFillBasics(user)
    await user.click(screen.getByTestId('savings-add-confirm'))

    expect(mockBridge.insertSavingsGoal).toHaveBeenCalledTimes(1)
    expect(lastInsertPayload()).toMatchObject({ periodValue: null, periodUnit: null })
  })

  it('selecting 个月 chip sends periodUnit month', async () => {
    const user = userEvent.setup()
    renderPage()
    await openFormAndFillBasics(user)
    await user.type(screen.getByTestId('savings-new-period'), '6')
    await user.click(screen.getByTestId('savings-period-unit-month'))
    await user.click(screen.getByTestId('savings-add-confirm'))

    expect(lastInsertPayload()).toMatchObject({ periodValue: 6, periodUnit: 'month' })
  })

  it('deselecting both chips means no period even with a number', async () => {
    const user = userEvent.setup()
    renderPage()
    await openFormAndFillBasics(user)
    await user.type(screen.getByTestId('savings-new-period'), '12')
    await user.click(screen.getByTestId('savings-period-unit-week')) // 默认选中 → 再点取消
    await user.click(screen.getByTestId('savings-add-confirm'))

    expect(lastInsertPayload()).toMatchObject({ periodValue: null, periodUnit: null })
  })
})
