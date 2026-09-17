/**
 * MePage 组件测试 — T-M5-04c: 账号删除按钮与二次确认对话框；T16-5: 背景透明度与自定义背景。
 *
 * 覆盖：
 * - 删除按钮渲染在「危险区」分组内
 * - 点击按钮弹出确认对话框
 * - 输入非 DELETE 时「确认删除」按钮禁用
 * - 输入 DELETE 后「确认删除」按钮启用
 * - 成功路径：requestAccountDeletionToken → deleteAccount
 * - 失败路径：BridgeError.code 映射到中文错误信息
 * - T16-5（REQ-61）：透明度滑轴渲染/持久化/即时应用；自定义背景选图/取消/清除
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { BridgeError } from '../../platform/bridge/bridge-types'
import { clearThemeBackground } from '../../design/theme-backgrounds'

const mockRequestDeletionToken = vi.fn()
const mockDeleteAccount = vi.fn()
const mockPickThemeBackground = vi.fn()

// NativeBridge is instantiated inside MePage with `new NativeBridge()`, so
// we mock the module and construct an object that returns our vi.fns.
// BridgeError is the *real* class — MePage's `instanceof` check needs it.
vi.mock('../../platform/bridge/native-bridge', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../platform/bridge/native-bridge')>()
  class MockBridge {
    requestAccountDeletionToken = mockRequestDeletionToken
    deleteAccount = mockDeleteAccount
    pickThemeBackground = mockPickThemeBackground
  }
  return {
    ...actual,
    NativeBridge: MockBridge as unknown as typeof actual.NativeBridge,
  }
})

import { MePage } from '../../features/settings/MePage'

const renderPage = () =>
  render(
    <MemoryRouter>
      <MePage />
    </MemoryRouter>,
  )

describe('MePage — T-M5-04c 账号删除', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockRequestDeletionToken.mockResolvedValue({
      confirmToken: 'tok-123',
      expiresAt: '2026-09-07T10:05:00Z',
    })
    mockDeleteAccount.mockResolvedValue({
      status: 'frozen',
      frozenAt: '2026-09-07T10:00:00Z',
      message: 'Account frozen',
    })
  })

  it('渲染「删除账号」按钮', () => {
    renderPage()
    expect(screen.getByText('删除账号')).toBeInTheDocument()
  })

  it('点击「删除账号」弹出确认对话框', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByText('删除账号'))
    expect(screen.getByText(/此操作不可撤销/)).toBeInTheDocument()
    expect(screen.getByPlaceholderText('DELETE')).toBeInTheDocument()
  })

  it('未输入 DELETE 时「确认删除」按钮禁用', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByText('删除账号'))
    const confirmBtn = screen.getByRole('button', { name: /确认删除/ })
    expect(confirmBtn).toBeDisabled()
  })

  it('输入 DELETE 后「确认删除」按钮启用', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByText('删除账号'))
    const input = screen.getByPlaceholderText('DELETE')
    await user.type(input, 'DELETE')
    const confirmBtn = screen.getByRole('button', { name: /确认删除/ })
    expect(confirmBtn).toBeEnabled()
  })

  it('输入不完整时按钮仍禁用', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByText('删除账号'))
    const input = screen.getByPlaceholderText('DELETE')
    await user.type(input, 'DEL')
    const confirmBtn = screen.getByRole('button', { name: /确认删除/ })
    expect(confirmBtn).toBeDisabled()
  })

  it('成功路径调用 requestAccountDeletionToken 再 deleteAccount，并关闭对话框', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByText('删除账号'))
    await user.type(screen.getByPlaceholderText('DELETE'), 'DELETE')
    await user.click(screen.getByRole('button', { name: /确认删除/ }))
    expect(mockRequestDeletionToken).toHaveBeenCalledTimes(1)
    expect(mockDeleteAccount).toHaveBeenCalledTimes(1)
    expect(mockDeleteAccount).toHaveBeenCalledWith('tok-123')
    // T-M5-04c review P3-8: 成功后对话框必须关闭，避免 Kotlin 跳转
    // 失败时 UI 卡死。
    await waitFor(() =>
      expect(screen.queryByPlaceholderText('DELETE')).not.toBeInTheDocument(),
    )
  })

  it('失败路径：invalid_token 显示中文错误信息', async () => {
    mockDeleteAccount.mockRejectedValueOnce(new BridgeError('invalid_token', 'Invalid confirmation token', 'req-1'))
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByText('删除账号'))
    await user.type(screen.getByPlaceholderText('DELETE'), 'DELETE')
    await user.click(screen.getByRole('button', { name: /确认删除/ }))
    expect(await screen.findByText(/确认码已失效/)).toBeInTheDocument()
  })

  it('失败路径：deletion_pending 显示中文错误信息', async () => {
    mockDeleteAccount.mockRejectedValueOnce(new BridgeError('deletion_pending', 'Already pending', 'req-1'))
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByText('删除账号'))
    await user.type(screen.getByPlaceholderText('DELETE'), 'DELETE')
    await user.click(screen.getByRole('button', { name: /确认删除/ }))
    expect(await screen.findByText(/账号已在删除流程中/)).toBeInTheDocument()
  })

  it('失败路径：auth_lost 显示中文错误信息', async () => {
    mockDeleteAccount.mockRejectedValueOnce(new BridgeError('auth_lost', 'Auth lost', 'req-1'))
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByText('删除账号'))
    await user.type(screen.getByPlaceholderText('DELETE'), 'DELETE')
    await user.click(screen.getByRole('button', { name: /确认删除/ }))
    expect(await screen.findByText(/登录已失效/)).toBeInTheDocument()
  })
})

describe('MePage — T16-5（REQ-61）背景透明度与自定义背景', () => {
  const pickUri = 'data:image/jpeg;base64,UElDSw=='

  beforeEach(async () => {
    vi.clearAllMocks()
    localStorage.clear()
    // 固定手动昼主题，避免 auto 模式随时段漂移
    localStorage.setItem('aozora-theme', JSON.stringify({ mode: 'manual', manual: 'day', scrim: 0 }))
    document.documentElement.removeAttribute('style')
    document.body.removeAttribute('style')
    await clearThemeBackground('day')
    mockPickThemeBackground.mockResolvedValue({ dataUri: pickUri, size: 4096 })
  })

  it('渲染背景透明度滑轴，初值来自存储', () => {
    localStorage.setItem('aozora-theme', JSON.stringify({ mode: 'manual', manual: 'day', scrim: 35 }))
    renderPage()
    expect(screen.getByTestId('bg-opacity-slider')).toHaveValue('35')
    expect(screen.getByTestId('bg-opacity-value')).toHaveTextContent('35%')
  })

  it('滑轴变更写入 localStorage 同键并即时应用 --bg-scrim', () => {
    renderPage()
    fireEvent.change(screen.getByTestId('bg-opacity-slider'), { target: { value: '40' } })
    expect(JSON.parse(localStorage.getItem('aozora-theme')!).scrim).toBe(40)
    expect(document.documentElement.style.getPropertyValue('--bg-scrim')).not.toBe('')
  })

  it('点击「选自定义背景」调用 bridge（gallery）并把图应用到 body', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByTestId('theme-pick-background'))
    expect(mockPickThemeBackground).toHaveBeenCalledWith('gallery')
    await waitFor(() =>
      expect(document.body.style.backgroundImage).toContain(pickUri),
    )
  })

  it('相册取消（cancelled）无副作用、无错误提示', async () => {
    mockPickThemeBackground.mockRejectedValueOnce(new BridgeError('cancelled', 'cancelled', 'req-1'))
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByTestId('theme-pick-background'))
    expect(document.body.style.backgroundImage).not.toContain(pickUri)
    expect(screen.queryByText(/背景选择失败/)).not.toBeInTheDocument()
    expect(screen.queryByTestId('theme-clear-background')).not.toBeInTheDocument()
  })

  it('保存成功后出现「清除自定义背景」，点击后移除 body 覆盖', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByTestId('theme-pick-background'))
    const clearBtn = await screen.findByTestId('theme-clear-background')
    await user.click(clearBtn)
    await waitFor(() =>
      expect(document.body.style.backgroundImage).not.toContain(pickUri),
    )
    expect(screen.queryByTestId('theme-clear-background')).not.toBeInTheDocument()
  })

  it('失败路径：unavailable 显示环境不支持提示', async () => {
    mockPickThemeBackground.mockRejectedValueOnce(new BridgeError('unavailable', 'no picker', 'req-1'))
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByTestId('theme-pick-background'))
    expect(await screen.findByText(/当前环境不支持选择照片/)).toBeInTheDocument()
  })
})
