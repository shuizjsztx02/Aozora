/**
 * AutoBookSettingsPage 测试 — T21-7 / REQ-83。
 *
 * 覆盖（对照卡片「用例：三态渲染 × 两种授权、开关读写、未授权时开关不可用且给出引导」）：
 *  - 三态渲染（未授权 / 已启用 / 已授权但停用）× 两类授权（无障碍 / 通知）
 *  - 总开关读写（toggle → autobook.set_enabled）
 *  - 两授权各自独立：只授一个，开关仍可用（决策 #10）
 *  - 两类都未授权时：开关 disabled + 给出引导文案
 *  - 「前往系统设置」按钮 → permissions.openSettings 带正确 target
 *  - 文案红线（决策 #20）：不出现「100% 自动」「绝不遗漏」
 *
 * 不覆盖：
 *  - 真机三态切换与跳系统页（决策 #19，归 T21-9 收口）
 *  - PermissionManager 三态判定（Kotlin 侧 PermissionManagerTest.kt）
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { AutoBookSettingsPage } from '../../features/settings/AutoBookSettingsPage'
import type { AutoBookStatusData, SettingsAuthStatus } from '../../platform/bridge/bridge-types'

const mockBridge = vi.hoisted(() => ({
  getAutoBookStatus: vi.fn(),
  setAutoBookEnabled: vi.fn().mockResolvedValue({ success: true, enabled: true }),
  openPermissionSettings: vi.fn().mockResolvedValue({ success: true }),
}))
vi.mock('../../platform/hooks/use-bridge', () => ({ useBridge: () => mockBridge }))

/** 构造一份状态；缺省两授权均「已启用」。 */
const mkStatus = (over: Partial<AutoBookStatusData> = {}): AutoBookStatusData => ({
  enabled: false,
  accessibilityStatus: 'enabled',
  notificationStatus: 'enabled',
  accessibilityGranted: true,
  notificationGranted: true,
  todayCaptureCount: 3,
  pendingConfirmCount: 1,
  ...over,
})

function setup(over: Partial<AutoBookStatusData> = {}) {
  mockBridge.getAutoBookStatus.mockResolvedValue(mkStatus(over))
}

function renderPage() {
  return render(
    <MemoryRouter>
      <AutoBookSettingsPage />
    </MemoryRouter>,
  )
}

describe('AutoBookSettingsPage — T21-7 / REQ-83', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // ─── 三态渲染 × 两类授权 ───

  it('无障碍「已启用」态 → 显示已启用徽标，无引导按钮', async () => {
    setup({ accessibilityStatus: 'enabled' })
    renderPage()
    const row = await screen.findByTestId('autobook-auth-accessibility')
    expect(row).toHaveTextContent('已启用')
    expect(screen.queryByTestId('autobook-auth-accessibility-button')).not.toBeInTheDocument()
  })

  it('无障碍「未授权」态 → 显示未授权徽标 + 引导按钮', async () => {
    setup({ accessibilityStatus: 'unauthorized', accessibilityGranted: false })
    renderPage()
    const row = await screen.findByTestId('autobook-auth-accessibility')
    expect(row).toHaveTextContent('未授权')
    expect(screen.getByTestId('autobook-auth-accessibility-button')).toBeInTheDocument()
  })

  it('无障碍「已授权但停用」态 → 显示停用徽标 + 「重新启用」引导文案', async () => {
    setup({ accessibilityStatus: 'enabled_but_disabled', accessibilityGranted: false })
    renderPage()
    const row = await screen.findByTestId('autobook-auth-accessibility')
    expect(row).toHaveTextContent('已授权但停用')
    expect(row).toHaveTextContent(/曾被停用/)
    expect(screen.getByTestId('autobook-auth-accessibility-button')).toBeInTheDocument()
  })

  it('通知「已授权但停用」态 → 显示停用徽标 + 引导按钮', async () => {
    setup({ notificationStatus: 'enabled_but_disabled', notificationGranted: false })
    renderPage()
    const row = await screen.findByTestId('autobook-auth-notification')
    expect(row).toHaveTextContent('已授权但停用')
    expect(screen.getByTestId('autobook-auth-notification-button')).toBeInTheDocument()
  })

  it('通知「未授权」态 → 显示未授权徽标 + 引导按钮', async () => {
    setup({ notificationStatus: 'unauthorized', notificationGranted: false })
    renderPage()
    const row = await screen.findByTestId('autobook-auth-notification')
    expect(row).toHaveTextContent('未授权')
    expect(screen.getByTestId('autobook-auth-notification-button')).toBeInTheDocument()
  })

  // ─── 开关读写 ───

  it('开关初始为关 → 点击后调 set_enabled(true)', async () => {
    setup({ enabled: false })
    renderPage()
    const toggle = await screen.findByTestId('autobook-toggle')
    fireEvent.click(toggle)
    await waitFor(() => expect(mockBridge.setAutoBookEnabled).toHaveBeenCalledWith(true))
  })

  it('开关初始为开 → 点击后调 set_enabled(false)', async () => {
    setup({ enabled: true })
    renderPage()
    const toggle = await screen.findByTestId('autobook-toggle')
    fireEvent.click(toggle)
    await waitFor(() => expect(mockBridge.setAutoBookEnabled).toHaveBeenCalledWith(false))
  })

  // ─── 两授权各自独立（决策 #10） ───

  it('只授通知（无障碍未授权）→ 开关仍可用', async () => {
    setup({
      accessibilityStatus: 'unauthorized',
      accessibilityGranted: false,
      notificationStatus: 'enabled',
      notificationGranted: true,
    })
    renderPage()
    const toggle = await screen.findByTestId('autobook-toggle')
    expect(toggle).not.toBeDisabled()
  })

  it('只授无障碍（通知未授权）→ 开关仍可用', async () => {
    setup({
      accessibilityStatus: 'enabled',
      accessibilityGranted: true,
      notificationStatus: 'unauthorized',
      notificationGranted: false,
    })
    renderPage()
    const toggle = await screen.findByTestId('autobook-toggle')
    expect(toggle).not.toBeDisabled()
  })

  // ─── 两类都未授权：开关不可用 + 引导 ───

  it('两类都未授权 → 开关 disabled + 给出引导文案', async () => {
    setup({
      accessibilityStatus: 'unauthorized',
      accessibilityGranted: false,
      notificationStatus: 'unauthorized',
      notificationGranted: false,
    })
    renderPage()
    const toggle = await screen.findByTestId('autobook-toggle')
    expect(toggle).toBeDisabled()
    expect(screen.getByText(/请先在下方启用至少一项授权/)).toBeInTheDocument()
  })

  it('两类都未授权 → 点击开关不触发 set_enabled', async () => {
    setup({
      accessibilityStatus: 'unauthorized',
      accessibilityGranted: false,
      notificationStatus: 'unauthorized',
      notificationGranted: false,
    })
    renderPage()
    const toggle = await screen.findByTestId('autobook-toggle')
    fireEvent.click(toggle)
    // 等一拍确认没有被调用
    await new Promise(r => setTimeout(r, 20))
    expect(mockBridge.setAutoBookEnabled).not.toHaveBeenCalled()
  })

  it('「已授权但停用」也算曾授予 → 开关可用（用户重新启用即可恢复）', async () => {
    setup({
      accessibilityStatus: 'enabled_but_disabled',
      accessibilityGranted: false,
      notificationStatus: 'unauthorized',
      notificationGranted: false,
    })
    renderPage()
    const toggle = await screen.findByTestId('autobook-toggle')
    expect(toggle).not.toBeDisabled()
  })

  // ─── 跳系统设置页（target 正确） ───

  it('无障碍引导按钮 → openPermissionSettings("accessibility")', async () => {
    setup({ accessibilityStatus: 'unauthorized', accessibilityGranted: false })
    renderPage()
    fireEvent.click(await screen.findByTestId('autobook-auth-accessibility-button'))
    await waitFor(() => expect(mockBridge.openPermissionSettings).toHaveBeenCalledWith('accessibility'))
  })

  it('通知引导按钮 → openPermissionSettings("notification_listener")', async () => {
    setup({ notificationStatus: 'unauthorized', notificationGranted: false })
    renderPage()
    fireEvent.click(await screen.findByTestId('autobook-auth-notification-button'))
    await waitFor(() => expect(mockBridge.openPermissionSettings).toHaveBeenCalledWith('notification_listener'))
  })

  // ─── 计数展示 ───

  it('今日捕获数与待确认数正确渲染', async () => {
    setup({ todayCaptureCount: 7, pendingConfirmCount: 2 })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('autobook-today-count')).toHaveTextContent('7'))
    expect(screen.getByTestId('autobook-pending-count')).toHaveTextContent('2')
  })

  // ─── 文案红线（决策 #20） ───

  it('文案不得出现「100% 自动」「绝不遗漏」', async () => {
    setup()
    const { container } = renderPage()
    await screen.findByTestId('autobook-toggle')
    const text = container.textContent ?? ''
    expect(text).not.toContain('100%')
    expect(text).not.toContain('绝不遗漏')
    expect(text).not.toContain('零遗漏')
  })

  it('用途说明含「不操作任何第三方 App」的显著披露', async () => {
    setup()
    const { container } = renderPage()
    await screen.findByTestId('autobook-toggle')
    expect(container.textContent).toContain('不操作任何第三方 App')
  })

  // ─── 桥失败降级 ───

  it('getAutoBookStatus 失败 → 页面不崩，开关降级为不可用', async () => {
    mockBridge.getAutoBookStatus.mockRejectedValue(new Error('bridge down'))
    renderPage()
    const toggle = await screen.findByTestId('autobook-toggle')
    // 失败时两授权默认 unauthorized → 开关不可用
    expect(toggle).toBeDisabled()
  })
})

// 类型引用（确保 SettingsAuthStatus 导出可用）
const _typeCheck: SettingsAuthStatus[] = ['unauthorized', 'enabled', 'enabled_but_disabled']
void _typeCheck
