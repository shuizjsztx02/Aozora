/**
 * AccountsPage 组件测试（T7-5 / REQ-5）。
 *
 * 覆盖：
 * - 新建走 insertAccount 封装：payload 含 accountType，id/mutationId 均为 UUID
 * - 点击列表行进入编辑态（表单预填），保存走 updateAccount 且 version = 原+1
 * - 自定义分类：回车加入 chip 并选中，提交进 payload
 * - 编辑态同名检测排除自身
 * - 失败时错误可见、表单与输入保留
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { AccountsPage } from '../../features/settings/AccountsPage'

const mockBridge = vi.hoisted(() => ({
  // T9-5b（REQ-11）：meta.get/set mock（默认无默认选择）
  getMeta: vi.fn().mockResolvedValue({}),
  setMeta: vi.fn().mockResolvedValue({ success: true }),
  // T9-6（REQ-13）：IconPicker 照片选择
  pickIcon: vi.fn(),
  call: vi.fn(),
  insertAccount: vi.fn(),
  updateAccount: vi.fn(),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

const renderPage = () =>
  render(
    <MemoryRouter>
      <AccountsPage />
    </MemoryRouter>
  )

describe('AccountsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.call.mockResolvedValue([])
    mockBridge.insertAccount.mockResolvedValue({ id: 'acc-new' })
    mockBridge.updateAccount.mockResolvedValue({ id: 'a1' })
  })

  it('creates an account with accountType via insertAccount (UUID ids)', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('＋ 添加账户'))
    await user.type(screen.getByPlaceholderText('账户名称'), '工资卡')
    await user.click(screen.getByRole('button', { name: '储蓄卡' }))
    await user.click(screen.getByRole('button', { name: '添加' }))

    await waitFor(() => expect(mockBridge.insertAccount).toHaveBeenCalledTimes(1))
    const [account, mutationId] = mockBridge.insertAccount.mock.calls[0]
    expect(account).toMatchObject({ ownerId: 'local', name: '工资卡', accountType: '储蓄卡', version: 1 })
    // 第六轮 Bug-5.2：实体 id 与 mutationId 必须为 UUID（服务端 sync/ai 契约）
    expect(account.id).toMatch(UUID_RE)
    expect(mutationId).toMatch(UUID_RE)
    // 成功后表单关闭
    await waitFor(() => expect(screen.queryByTestId('account-form')).not.toBeInTheDocument())
  })

  it('row click opens a prefilled edit form and saves via updateAccount with version+1', async () => {
    mockBridge.call.mockResolvedValue([
      { id: 'a1', name: '旧卡片', icon: '💳', version: 3, accountType: '微信' },
    ])
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('旧卡片'))
    // 预填名称 + 列表行显示分类括注
    expect(screen.getByDisplayValue('旧卡片')).toBeInTheDocument()
    expect(screen.getByText('(微信)')).toBeInTheDocument()
    await user.clear(screen.getByPlaceholderText('账户名称'))
    await user.type(screen.getByPlaceholderText('账户名称'), '新卡片')
    await user.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(mockBridge.updateAccount).toHaveBeenCalledTimes(1))
    const [account, mutationId] = mockBridge.updateAccount.mock.calls[0]
    expect(account).toMatchObject({
      id: 'a1', ownerId: 'local', name: '新卡片', icon: '💳', accountType: '微信',
      version: 4, // 原 version 3 + 1（native 侧要求递增）
    })
    expect(mutationId).toMatch(UUID_RE)
  })

  it('adding a custom category via Enter selects it and lands in the payload', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('＋ 添加账户'))
    await user.type(screen.getByPlaceholderText('账户名称'), '钱包')
    await user.click(screen.getByRole('button', { name: '＋ 自定义' }))
    await user.type(screen.getByTestId('custom-type-input'), '数字人民币{Enter}')
    // chip 出现（预置 8 类之外）
    expect(await screen.findByRole('button', { name: '数字人民币' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '添加' }))
    await waitFor(() => expect(mockBridge.insertAccount).toHaveBeenCalledTimes(1))
    expect(mockBridge.insertAccount.mock.calls[0][0]).toMatchObject({ accountType: '数字人民币' })
  })

  it('duplicate-name check excludes the account itself when editing', async () => {
    mockBridge.call.mockResolvedValue([
      { id: 'a1', name: '旧卡片', icon: '💳', version: 1, accountType: null },
    ])
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('旧卡片'))
    // 不改名称直接保存 → 不应报"已存在"（同名检测排除自身）
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(mockBridge.updateAccount).toHaveBeenCalledTimes(1))
    expect(screen.queryByText('该账户名已存在')).not.toBeInTheDocument()
  })

  it('shows a visible error and keeps the form open when create fails', async () => {
    mockBridge.insertAccount.mockRejectedValue(new Error('bridge internal_error'))
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('＋ 添加账户'))
    await user.type(screen.getByPlaceholderText('账户名称'), '失败账户')
    await user.click(screen.getByRole('button', { name: '添加' }))

    await waitFor(() => expect(screen.getByText(/创建账户失败/)).toBeInTheDocument())
    // 表单保持打开，输入保留
    expect(screen.getByPlaceholderText('账户名称')).toBeInTheDocument()
  })
})

// ─── T9-5b（REQ-11）：默认勾选 + 长按排序 ───

describe('AccountsPage · 排序与默认（REQ-11）', () => {
  const TWO = [
    { id: 'a1', name: '微信', icon: '💬', version: 1, accountType: null },
    { id: 'a2', name: '支付宝', icon: '💰', version: 1, accountType: null },
  ]

  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.call.mockResolvedValue(TWO)
    mockBridge.setMeta.mockResolvedValue({ success: true })
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('checking default calls setMeta(default_account_id, id)', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByTestId('account-default-a1'))
    expect(mockBridge.setMeta).toHaveBeenCalledWith('default_account_id', 'a1')
  })

  it('isDefault row renders 默认 badge', async () => {
    mockBridge.call.mockResolvedValue([{ ...TWO[0], isDefault: true }, TWO[1]])
    renderPage()
    const badge = await screen.findByTestId('account-default-badge-a1')
    expect(badge.textContent).toBe('默认')
  })

  it('long press 500ms enters reorder mode; plain click still opens edit', async () => {
    renderPage()
    const row = await screen.findByTestId('account-row-a2')
    vi.useFakeTimers()
    fireEvent.pointerDown(row, { clientX: 10, clientY: 10 })
    act(() => { vi.advanceTimersByTime(500) })
    fireEvent.pointerUp(row)
    vi.useRealTimers()
    fireEvent.click(row) // 长按后跟随的 click 应被吞掉，不进编辑
    expect(screen.getByTestId('account-reorder-done')).toBeInTheDocument()
    expect(screen.queryByTestId('account-form')).toBeNull()
  })

  it('↑ moves a row and persists the full id order', async () => {
    renderPage()
    const row = await screen.findByTestId('account-row-a2')
    vi.useFakeTimers()
    fireEvent.pointerDown(row, { clientX: 10, clientY: 10 })
    act(() => { vi.advanceTimersByTime(500) })
    fireEvent.pointerUp(row)
    vi.useRealTimers()
    fireEvent.click(screen.getByTestId('account-move-up-a2'))
    await waitFor(() =>
      expect(mockBridge.setMeta).toHaveBeenCalledWith('account_order', JSON.stringify(['a2', 'a1']))
    )
  })
})

// ─── T9-6（REQ-13）：共享 IconPicker — 自定义文本覆写 emoji + 相册照片 ───

describe('AccountsPage · 图标选择（REQ-13）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.call.mockResolvedValue([])
    mockBridge.insertAccount.mockResolvedValue({ id: 'acc-new' })
    mockBridge.pickIcon.mockResolvedValue({ dataUri: 'data:image/jpeg;base64,QVJD', size: 4 })
  })

  it('custom icon text overrides the emoji default in the payload', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('＋ 添加账户'))
    await user.type(screen.getByPlaceholderText('账户名称'), '食堂卡')
    // 决策 #5：文本框非空优先于 emoji（初始 '💰' 让位）
    await user.type(screen.getByTestId('custom-icon-input'), '🍱')
    await user.click(screen.getByRole('button', { name: '添加' }))

    await waitFor(() => expect(mockBridge.insertAccount).toHaveBeenCalledTimes(1))
    expect(mockBridge.insertAccount.mock.calls[0][0].icon).toBe('🍱')
  })

  it('gallery photo icon lands as data: URI in the payload', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('＋ 添加账户'))
    await user.type(screen.getByPlaceholderText('账户名称'), '相册户')
    await user.click(screen.getByTestId('icon-pick-photo'))
    await waitFor(() => expect(mockBridge.pickIcon).toHaveBeenCalledWith('gallery'))
    await user.click(screen.getByRole('button', { name: '添加' }))

    await waitFor(() => expect(mockBridge.insertAccount).toHaveBeenCalledTimes(1))
    expect(mockBridge.insertAccount.mock.calls[0][0].icon).toBe('data:image/jpeg;base64,QVJD')
  })

  it('existing non-preset icon is backfilled into the custom text box on edit', async () => {
    mockBridge.call.mockResolvedValue([
      { id: 'a1', name: '旧卡片', icon: '🏦', version: 1, accountType: null },
    ])
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('旧卡片'))
    // 🏦 不在 emoji 宫格 → 回填自定义文本框（保留"非预置回填"逻辑）
    await waitFor(() =>
      expect(screen.getByTestId('custom-icon-input')).toHaveValue('🏦')
    )
  })
})
