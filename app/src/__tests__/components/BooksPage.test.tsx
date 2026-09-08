/**
 * BooksPage 组件测试（Bug-3 回归防护）。
 *
 * 覆盖：
 * - 点击「添加」走 bridge.insertBook 封装（{book} + mutationId 包裹契约），成功后关闭表单
 * - 创建失败时表单内显示可见错误且不关闭表单（错误不再被静默吞掉）
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { BooksPage } from '../../features/settings/BooksPage'

const mockBridge = vi.hoisted(() => ({
  call: vi.fn(),
  insertBook: vi.fn(),
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
