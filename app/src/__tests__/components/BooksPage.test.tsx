/**
 * BooksPage 组件测试（Bug-3 回归防护）。
 *
 * 覆盖：
 * - 点击「添加」走 bridge.insertBook 封装（{book} + mutationId 包裹契约），成功后关闭表单
 * - 创建失败时表单内显示可见错误且不关闭表单（错误不再被静默吞掉）
 * - T14-2（REQ-39）：点行开编辑表单回填名称、保存走 updateBook（version+1）、
 *   master 行不给编辑入口、同名检测编辑态排除自身
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { BooksPage } from '../../features/settings/BooksPage'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

const mockBridge = vi.hoisted(() => ({
  // T9-5b（REQ-11）：meta.get/set mock（默认无默认选择）
  getMeta: vi.fn().mockResolvedValue({}),
  setMeta: vi.fn().mockResolvedValue({ success: true }),
  // T9-6（REQ-13）：IconPicker 照片选择
  pickIcon: vi.fn(),
  call: vi.fn(),
  insertBook: vi.fn(),
  // T14-2（REQ-39）：账本编辑
  updateBook: vi.fn(),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

const renderPage = () =>
  render(
    <MemoryRouter>
      <BooksPage />
    </MemoryRouter>
  )

describe('BooksPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // book.list → 空列表
    mockBridge.call.mockResolvedValue([])
    mockBridge.insertBook.mockResolvedValue({ id: 'book-new' })
  })

  it('creates a book via insertBook wrapper with a mutationId', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('＋ 添加账本'))
    await user.type(screen.getByPlaceholderText('账本名称'), '测试')
    await user.click(screen.getByRole('button', { name: '添加' }))

    await waitFor(() => expect(mockBridge.insertBook).toHaveBeenCalledTimes(1))
    const [book, mutationId] = mockBridge.insertBook.mock.calls[0]
    expect(book).toMatchObject({ ownerId: 'local', name: '测试', icon: '📒', version: 1 })
    // 第六轮 Bug-5.2：实体 id 必须为 UUID（服务端 sync/ai 契约）
    expect(book.id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    expect(mutationId).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    // 成功后表单关闭
    await waitFor(() =>
      expect(screen.queryByPlaceholderText('账本名称')).not.toBeInTheDocument()
    )
  })

  it('shows a visible error and keeps the form open when create fails', async () => {
    mockBridge.insertBook.mockRejectedValue(new Error('bridge internal_error'))
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('＋ 添加账本'))
    await user.type(screen.getByPlaceholderText('账本名称'), '测试')
    await user.click(screen.getByRole('button', { name: '添加' }))

    await waitFor(() => expect(screen.getByText(/创建账本失败/)).toBeInTheDocument())
    // 表单保持打开，输入保留
    expect(screen.getByPlaceholderText('账本名称')).toBeInTheDocument()
  })

  // T7-6 / REQ-6：总账本行加徽标与副文案，普通账本行不加
  it('marks the master book row with a badge and note', async () => {
    mockBridge.call.mockResolvedValue([
      { id: 'm1', ownerId: 'local', name: '总账本', icon: '📊', version: 1, isMaster: true },
      { id: 'b1', ownerId: 'local', name: '默认账本', icon: '📒', version: 1 },
    ])
    renderPage()
    await waitFor(() => expect(screen.getByText('总账本')).toBeInTheDocument())
    expect(screen.getByText('自动汇总，不可记账')).toBeInTheDocument()
    // 徽标只出现在 master 行
    expect(screen.getAllByText('汇总 · 所有账本')).toHaveLength(1)
    expect(screen.getAllByText('自动汇总，不可记账')).toHaveLength(1)
  })
})

// ─── T14-2（REQ-39 / 决策 #3）：点击行编辑（照搬 AccountsPage 模式） ───

describe('BooksPage · 点击编辑（T14-2 / REQ-39）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.call.mockResolvedValue([
      { id: 'm1', ownerId: 'local', name: '总账本', icon: '📊', version: 1, isMaster: true },
      { id: 'b1', ownerId: 'local', name: '旧账本', icon: '📒', version: 3 },
    ])
    mockBridge.updateBook.mockResolvedValue({ id: 'b1' })
    mockBridge.insertBook.mockResolvedValue({ id: 'book-new' })
  })

  it('row click opens a prefilled edit form and saves via updateBook with version+1', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('旧账本'))
    // 表单标题二态 + 名称/图标回填
    expect(screen.getByTestId('book-form')).toBeInTheDocument()
    expect(screen.getByText('编辑账本')).toBeInTheDocument()
    expect(screen.getByDisplayValue('旧账本')).toBeInTheDocument()
    await user.clear(screen.getByPlaceholderText('账本名称'))
    await user.type(screen.getByPlaceholderText('账本名称'), '新账本')
    await user.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(mockBridge.updateBook).toHaveBeenCalledTimes(1))
    const [book, mutationId] = mockBridge.updateBook.mock.calls[0]
    expect(book).toMatchObject({
      id: 'b1', ownerId: 'local', name: '新账本', icon: '📒',
      version: 4, // 原 version 3 + 1（native 侧要求递增）
    })
    expect(mutationId).toMatch(UUID_RE)
    // 成功后表单关闭
    await waitFor(() => expect(screen.queryByTestId('book-form')).not.toBeInTheDocument())
  })

  it('master row offers no edit entry — click does not open the form', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('总账本'))
    expect(screen.queryByTestId('book-form')).not.toBeInTheDocument()
    expect(mockBridge.updateBook).not.toHaveBeenCalled()
  })

  it('duplicate-name check excludes the book itself when editing', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('旧账本'))
    // 不改名称直接保存 → 不应报"已存在"（同名检测排除自身）
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(mockBridge.updateBook).toHaveBeenCalledTimes(1))
    expect(screen.queryByText('该账本名已存在')).not.toBeInTheDocument()
  })

  it('update failure shows a visible error and keeps the form open', async () => {
    mockBridge.updateBook.mockRejectedValue(new Error('bridge internal_error'))
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('旧账本'))
    await user.clear(screen.getByPlaceholderText('账本名称'))
    await user.type(screen.getByPlaceholderText('账本名称'), '新账本')
    await user.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(screen.getByText(/更新账本失败/)).toBeInTheDocument())
    expect(screen.getByDisplayValue('新账本')).toBeInTheDocument()
  })

  it('new-book path still goes through insertBook (add button opens empty form)', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('＋ 添加账本'))
    expect(screen.getByText('新建账本')).toBeInTheDocument()
    await user.type(screen.getByPlaceholderText('账本名称'), '新建的')
    await user.click(screen.getByRole('button', { name: '添加' }))
    await waitFor(() => expect(mockBridge.insertBook).toHaveBeenCalledTimes(1))
    expect(mockBridge.updateBook).not.toHaveBeenCalled()
    expect(mockBridge.insertBook.mock.calls[0][0]).toMatchObject({ name: '新建的', version: 1 })
  })
})

// ─── T9-5b（REQ-11）：默认勾选 + 长按排序 ───

describe('BooksPage · 排序与默认（REQ-11）', () => {
  const THREE = [
    { id: 'm1', ownerId: 'local', name: '总账本', icon: '📊', version: 1, isMaster: true },
    { id: 'b1', ownerId: 'local', name: '默认账本', icon: '📒', version: 1 },
    { id: 'b2', ownerId: 'local', name: '旅行', icon: '✈️', version: 1 },
  ]

  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.call.mockResolvedValue(THREE)
    mockBridge.setMeta.mockResolvedValue({ success: true })
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('checking default calls setMeta(default_book_id, id) and master row has no checkbox', async () => {
    const user = userEvent.setup()
    renderPage()
    const box = await screen.findByTestId('book-default-b1')
    await user.click(box)
    expect(mockBridge.setMeta).toHaveBeenCalledWith('default_book_id', 'b1')
    // 总账本不可记账 → 不提供默认勾选，也不提供排序按钮（决策 #4）
    expect(screen.queryByTestId('book-default-m1')).toBeNull()
  })

  it('isDefault row renders 默认 badge', async () => {
    mockBridge.call.mockResolvedValue([
      THREE[0],
      { ...THREE[1], isDefault: true },
      THREE[2],
    ])
    renderPage()
    const badge = await screen.findByTestId('book-default-badge-b1')
    expect(badge.textContent).toBe('默认')
  })

  it('long press 500ms enters reorder mode showing ↑↓ and 完成', async () => {
    renderPage()
    const row = await screen.findByTestId('book-row-b1')
    vi.useFakeTimers()
    fireEvent.pointerDown(row, { clientX: 10, clientY: 10 })
    act(() => { vi.advanceTimersByTime(500) })
    fireEvent.pointerUp(row)
    vi.useRealTimers()
    expect(screen.getByTestId('book-reorder-done')).toBeInTheDocument()
    expect(screen.getByTestId('book-move-up-b1')).toBeInTheDocument()
    expect(screen.getByTestId('book-move-down-b1')).toBeInTheDocument()
    // master 行无 ↑↓
    expect(screen.queryByTestId('book-move-up-m1')).toBeNull()
    expect(screen.queryByTestId('book-move-down-m1')).toBeNull()
  })

  it('↑ moves the row one slot up and persists full id order', async () => {
    renderPage()
    const row = await screen.findByTestId('book-row-b2')
    vi.useFakeTimers()
    fireEvent.pointerDown(row, { clientX: 10, clientY: 10 })
    act(() => { vi.advanceTimersByTime(500) })
    fireEvent.pointerUp(row)
    vi.useRealTimers()
    fireEvent.click(screen.getByTestId('book-move-up-b2'))
    await waitFor(() =>
      expect(mockBridge.setMeta).toHaveBeenCalledWith('book_order', JSON.stringify(['m1', 'b2', 'b1']))
    )
    // ↑ 对紧跟 master 的行禁用（不允许移入首位区）
    await waitFor(() => {
      const upB1 = screen.getByTestId('book-move-up-b1') as HTMLButtonElement
      expect(upB1.disabled).toBe(true)
    })
  })
})

// ─── T9-6（REQ-13）：共享 IconPicker — emoji 宫格 + 相册照片 ───

describe('BooksPage · 图标选择（REQ-13）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.call.mockResolvedValue([])
    mockBridge.insertBook.mockResolvedValue({ id: 'book-new' })
    mockBridge.pickIcon.mockResolvedValue({ dataUri: 'data:image/png;base64,AAAA', size: 4 })
  })

  it('gallery photo icon lands as data: URI in the insertBook payload', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('＋ 添加账本'))
    await user.type(screen.getByPlaceholderText('账本名称'), '相册本')
    await user.click(screen.getByTestId('icon-pick-photo'))
    await waitFor(() => expect(mockBridge.pickIcon).toHaveBeenCalledWith('gallery'))
    await user.click(screen.getByRole('button', { name: '添加' }))

    await waitFor(() => expect(mockBridge.insertBook).toHaveBeenCalledTimes(1))
    const [book] = mockBridge.insertBook.mock.calls[0]
    expect(book.icon).toMatch(/^data:image\/png;base64,/)
  })

  it('emoji picked from the shared grid is used as the book icon', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('＋ 添加账本'))
    await user.type(screen.getByPlaceholderText('账本名称'), '交通本')
    await user.click(within(screen.getByTestId('emoji-picker')).getByText('🚇'))
    await user.click(screen.getByRole('button', { name: '添加' }))

    await waitFor(() => expect(mockBridge.insertBook).toHaveBeenCalledTimes(1))
    expect(mockBridge.insertBook.mock.calls[0][0].icon).toBe('🚇')
  })
})
