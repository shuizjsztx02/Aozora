/**
 * ExportPage 组件测试 — T-M5-04b。
 *
 * 覆盖：
 * - 初始加载：账本列表加载 + 默认选中第一个
 * - 生成导出：点击按钮 → 调 bridge.createExport → 显示就绪状态
 * - 下载：就绪状态 → 点击下载 → bridge.downloadExport → 显示下载结果
 * - JSON 下载后对账：导出合计 == 本地合计 → 显示 "对账一致"
 * - JSON 下载后对账不一致 → 显示差额
 * - 失败：导出失败 → 显示错误 + 重试按钮
 * - 历史列表：渲染最近 5 条
 * - CSV 格式下载后不对账（无 reconciliation 显示）
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { ExportPage } from '../../features/settings/ExportPage'

const mockBridge = vi.hoisted(() => ({
  call: vi.fn(),
  createExport: vi.fn(),
  listExports: vi.fn(),
  getExport: vi.fn(),
  downloadExport: vi.fn(),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

const renderPage = () =>
  render(
    <MemoryRouter>
      <ExportPage />
    </MemoryRouter>
  )

const sampleBooks = [
  { id: 'book-1', name: '日常账本' },
  { id: 'book-2', name: '旅行账本' },
]

const sampleExport = {
  id: 'exp-1',
  bookId: 'book-1',
  format: 'json' as const,
  status: 'completed',
  createdAt: '2026-09-07T10:00:00Z',
  expiresAt: '2026-09-08T10:00:00Z',
  fileSize: 1234,
}

const sampleLocalTxs = [
  { id: 'tx-1', amountCents: 1000, type: 'expense', deletedAt: null },
  { id: 'tx-2', amountCents: 500, type: 'expense', deletedAt: null },
  { id: 'tx-3', amountCents: 200, type: 'income', deletedAt: null },
]

const sampleExportJson = JSON.stringify([
  { amount_cents: 1000, type: 'expense' },
  { amount_cents: 500, type: 'expense' },
  { amount_cents: 200, type: 'income' },
])

describe('ExportPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.call.mockImplementation(async (cmd: string) => {
      if (cmd === 'book.list') return sampleBooks
      if (cmd === 'transaction.listByBook') return sampleLocalTxs
      return []
    })
    mockBridge.listExports.mockResolvedValue([])
    mockBridge.createExport.mockResolvedValue(sampleExport)
    mockBridge.downloadExport.mockResolvedValue({
      localPath: '/Download/Aozora/export-book-1.json',
      filename: 'export-book-1.json',
      content: sampleExportJson,
    })
  })

  it('loads books and selects first by default', async () => {
    renderPage()
    expect(screen.getByText('加载中...')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('加载中...')).not.toBeInTheDocument())
    expect(screen.getByText('日常账本')).toBeInTheDocument()
    expect(screen.getByText('旅行账本')).toBeInTheDocument()
    // 默认选中第一个
    expect(screen.getByText('日常账本').parentElement?.querySelector('span:last-child')?.textContent).toBe('✓')
  })

  it('creates export on button click', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(screen.queryByText('加载中...')).not.toBeInTheDocument())

    await user.click(screen.getByText('生成导出'))
    await waitFor(() => expect(mockBridge.createExport).toHaveBeenCalledWith({
      bookId: 'book-1',
      format: 'json',
    }))
    await waitFor(() => expect(screen.getByText('导出已就绪')).toBeInTheDocument())
  })

  it('downloads and reconciles JSON', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(screen.queryByText('加载中...')).not.toBeInTheDocument())

    await user.click(screen.getByText('生成导出'))
    await waitFor(() => expect(screen.getByText('导出已就绪')).toBeInTheDocument())

    await user.click(screen.getByText('下载文件'))
    // 下载异步完成后显示对账结果
    await waitFor(() => expect(screen.getByText('✓ 对账一致')).toBeInTheDocument())
    expect(mockBridge.downloadExport).toHaveBeenCalledWith('exp-1')
    // 本地合计 = 1500, 导出合计 = 1500 — 两个 ¥15.00（本地 + 导出）
    expect(screen.getAllByText('¥15.00')).toHaveLength(2)
  })

  it('shows reconciliation mismatch when sums differ', async () => {
    mockBridge.downloadExport.mockResolvedValue({
      localPath: '/Download/Aozora/export-book-1.json',
      filename: 'export-book-1.json',
      content: JSON.stringify([
        { amount_cents: 999, type: 'expense' }, // 故意不一致
      ]),
    })

    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(screen.queryByText('加载中...')).not.toBeInTheDocument())

    await user.click(screen.getByText('生成导出'))
    await waitFor(() => expect(screen.getByText('导出已就绪')).toBeInTheDocument())
    await user.click(screen.getByText('下载文件'))

    await waitFor(() => expect(screen.getByText('✗ 对账不一致')).toBeInTheDocument())
  })

  it('shows failure state with retry button', async () => {
    mockBridge.createExport.mockRejectedValueOnce(new Error('network_error'))

    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(screen.queryByText('加载中...')).not.toBeInTheDocument())

    await user.click(screen.getByText('生成导出'))
    await waitFor(() => expect(screen.getByText('导出失败')).toBeInTheDocument())
    expect(screen.getByText('network_error')).toBeInTheDocument()
    expect(screen.getByText('重试')).toBeInTheDocument()
  })

  it('CSV download skips reconciliation', async () => {
    mockBridge.createExport.mockResolvedValue({ ...sampleExport, format: 'csv' })
    mockBridge.downloadExport.mockResolvedValue({
      localPath: '/Download/Aozora/export-book-1.csv',
      filename: 'export-book-1.csv',
      content: 'id,type,amount_cents\n1,expense,1000',
    })

    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(screen.queryByText('加载中...')).not.toBeInTheDocument())

    await user.click(screen.getByText('生成导出'))
    await waitFor(() => expect(screen.getByText('导出已就绪')).toBeInTheDocument())
    await user.click(screen.getByText('下载文件'))

    await waitFor(() => expect(screen.getByText('export-book-1.csv')).toBeInTheDocument())
    expect(screen.getByText(/CSV 文件已下载/)).toBeInTheDocument()
    expect(screen.queryByText('对账一致')).not.toBeInTheDocument()
  })

  it('renders history list', async () => {
    mockBridge.listExports.mockResolvedValue([sampleExport])
    renderPage()
    await waitFor(() => expect(screen.getByText('最近导出（1）')).toBeInTheDocument())
    expect(screen.getByText(/JSON · 1.2 KB/)).toBeInTheDocument()
  })

  it('excludes soft-deleted transactions from reconciliation', async () => {
    // 本地有一笔 soft-deleted，应被排除
    mockBridge.call.mockImplementation(async (cmd: string) => {
      if (cmd === 'book.list') return sampleBooks
      if (cmd === 'transaction.listByBook') return [
        ...sampleLocalTxs,
        { id: 'tx-del', amountCents: 9999, type: 'expense', deletedAt: 1725000000 },
      ]
      return []
    })

    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(screen.queryByText('加载中...')).not.toBeInTheDocument())
    await user.click(screen.getByText('生成导出'))
    await waitFor(() => expect(screen.getByText('导出已就绪')).toBeInTheDocument())
    await user.click(screen.getByText('下载文件'))

    await waitFor(() => expect(screen.getByText('✓ 对账一致')).toBeInTheDocument())
  })
})
