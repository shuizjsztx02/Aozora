/**
 * ReminderSettingsPage 组件测试（R-M4-22 测试覆盖补充）。
 *
 * 覆盖：
 * - 初始化加载 + 加载态显示
 * - 禁用时隐藏时间选择器，启用时显示并保持当前值
 * - 开关切换调用 setReminder
 * - 时间滚轮（小时 wrap、分钟调整）
 * - 通知权限未开启时显示横幅 + 点击「去开启」调用 requestPermission（R-M4-27 修复）
 * - setReminder 失败时开关回滚
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { ReminderSettingsPage } from '../../features/settings/ReminderSettingsPage'

const mockBridge = vi.hoisted(() => ({
  getReminderStatus: vi.fn(),
  setReminder: vi.fn(),
  checkReminderPermission: vi.fn(),
  requestPermission: vi.fn(),
  openPermissionSettings: vi.fn(),
  onPermissionResult: vi.fn(),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

const renderPage = () =>
  render(
    <MemoryRouter>
      <ReminderSettingsPage />
    </MemoryRouter>
  )

describe('ReminderSettingsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.getReminderStatus.mockResolvedValue({
      enabled: false,
      hour: 21,
      minute: 0,
      notificationPermission: true,
    })
    mockBridge.setReminder.mockResolvedValue({ success: true })
    mockBridge.checkReminderPermission.mockResolvedValue({ granted: true })
    mockBridge.requestPermission.mockResolvedValue({ pending: true })
    mockBridge.openPermissionSettings.mockResolvedValue({ success: true })
    mockBridge.onPermissionResult.mockReturnValue(() => {})
  })

  it('renders immediately then updates with loaded status', async () => {
    renderPage()
    // 页面结构立即渲染（不再以全屏 loading 阻塞）
    expect(screen.getByText('每日记账提醒')).toBeInTheDocument()
    await waitFor(() => expect(mockBridge.getReminderStatus).toHaveBeenCalledTimes(1))
  })

  it('hides time picker when disabled', async () => {
    renderPage()
    await waitFor(() =>
      expect(screen.queryByText('加载中...')).not.toBeInTheDocument(),
    )
    expect(screen.queryByText('提醒时间')).not.toBeInTheDocument()
  })

  it('shows time picker with saved values when enabled', async () => {
    mockBridge.getReminderStatus.mockResolvedValue({
      enabled: true,
      hour: 9,
      minute: 5,
      notificationPermission: true,
    })
    renderPage()
    await waitFor(() => expect(screen.getByText('09')).toBeInTheDocument())
    expect(screen.getByText('05')).toBeInTheDocument()
    expect(screen.getByText('提醒时间')).toBeInTheDocument()
  })

  it('toggle calls setReminder with new enabled state', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitFor(() =>
      expect(screen.queryByText('加载中...')).not.toBeInTheDocument(),
    )
    await user.click(screen.getByRole('checkbox'))
    expect(mockBridge.setReminder).toHaveBeenCalledWith(true, 21, 0)
  })

  it('hour up button wraps around 24h', async () => {
    mockBridge.getReminderStatus.mockResolvedValue({
      enabled: true,
      hour: 23,
      minute: 30,
      notificationPermission: true,
    })
    renderPage()
    await waitFor(() => expect(screen.getByText('23')).toBeInTheDocument())
    const upButtons = screen.getAllByText('▲')
    await userEvent.setup().click(upButtons[0])
    expect(mockBridge.setReminder).toHaveBeenCalledWith(true, 0, 30)
  })

  it('minute up button increments without hour change', async () => {
    mockBridge.getReminderStatus.mockResolvedValue({
      enabled: true,
      hour: 9,
      minute: 30,
      notificationPermission: true,
    })
    renderPage()
    await waitFor(() => expect(screen.getByText('09')).toBeInTheDocument())
    const upButtons = screen.getAllByText('▲')
    await userEvent.setup().click(upButtons[1])
    expect(mockBridge.setReminder).toHaveBeenCalledWith(true, 9, 31)
  })

  it('shows permission banner when notificationPermission is false', async () => {
    mockBridge.getReminderStatus.mockResolvedValue({
      enabled: false,
      hour: 21,
      minute: 0,
      notificationPermission: false,
    })
    renderPage()
    await waitFor(() =>
      expect(screen.getByText('通知权限未开启')).toBeInTheDocument(),
    )
  })

  // R-M4-27 修复：点击「去开启」应调用 requestPermission 而非仅 checkReminderPermission
  it('clicking 去开启 triggers requestPermission for POST_NOTIFICATIONS', async () => {
    mockBridge.getReminderStatus.mockResolvedValue({
      enabled: false,
      hour: 21,
      minute: 0,
      notificationPermission: false,
    })
    renderPage()
    await waitFor(() => expect(screen.getByText('去开启')).toBeInTheDocument())
    await userEvent.setup().click(screen.getByText('去开启'))
    expect(mockBridge.requestPermission).toHaveBeenCalledWith('POST_NOTIFICATIONS')
  })

  // R-M4-27: 权限结果回调处理 - granted 时更新状态
  it('updates hasPermission when onPermissionResult callback fires with granted', async () => {
    let permissionCallback: ((event: { permission: string; status: string }) => void) | null = null
    mockBridge.onPermissionResult.mockImplementation((cb) => {
      permissionCallback = cb
      return () => {}
    })
    mockBridge.getReminderStatus.mockResolvedValue({
      enabled: false,
      hour: 21,
      minute: 0,
      notificationPermission: false,
    })
    renderPage()
    await waitFor(() => expect(screen.getByText('去开启')).toBeInTheDocument())
    // 模拟权限授予回调
    expect(permissionCallback).not.toBeNull()
    permissionCallback!({ permission: 'POST_NOTIFICATIONS', status: 'granted' })
    await waitFor(() =>
      expect(screen.queryByText('去开启')).not.toBeInTheDocument(),
    )
  })

  // R-M4-27: 权限永久拒绝时引导到系统设置
  it('opens app settings when permission is permanently denied', async () => {
    let permissionCallback: ((event: { permission: string; status: string }) => void) | null = null
    mockBridge.onPermissionResult.mockImplementation((cb) => {
      permissionCallback = cb
      return () => {}
    })
    mockBridge.getReminderStatus.mockResolvedValue({
      enabled: false,
      hour: 21,
      minute: 0,
      notificationPermission: false,
    })
    renderPage()
    await waitFor(() => expect(screen.getByText('去开启')).toBeInTheDocument())
    // 模拟永久拒绝回调
    permissionCallback!({ permission: 'POST_NOTIFICATIONS', status: 'permanently_denied' })
    await waitFor(() =>
      expect(mockBridge.openPermissionSettings).toHaveBeenCalled(),
    )
  })

  it('toggle reverts when setReminder fails', async () => {
    mockBridge.setReminder.mockRejectedValue(new Error('bridge fail'))
    const user = userEvent.setup()
    renderPage()
    await waitFor(() =>
      expect(screen.queryByText('加载中...')).not.toBeInTheDocument(),
    )
    const checkbox = screen.getByRole<HTMLInputElement>('checkbox')
    expect(checkbox.checked).toBe(false)
    await user.click(checkbox)
    await waitFor(() => expect(checkbox.checked).toBe(false))
  })
})
