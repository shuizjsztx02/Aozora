/**
 * SecurityPage 组件测试 — 第十轮 T10-3（REQ-19 / REQ-20 上半）。
 *
 * 覆盖（卡验收）：
 * - 游客/已登录身份卡渲染（脱敏手机号、兜底文案）
 * - 退出登录：确认弹框含"清除本机数据"提示 → logoutAuth → navigate('/onboarding')
 * - 切换账号：logout → navigate('/login')
 * - 修改昵称：updateProfile payload（trim 后值）
 * - 修改头像：pickIcon('gallery') → setMeta('avatar_uri', dataUri)；cancelled 静默
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BridgeError } from '../../platform/bridge/bridge-types'

const mockBridge = vi.hoisted(() => ({
  getAuthStatus: vi.fn(),
  getMeta: vi.fn(),
  getMe: vi.fn(),
  updateProfile: vi.fn(),
  pickIcon: vi.fn(),
  setMeta: vi.fn(),
  logoutAuth: vi.fn(),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

const mockNavigate = vi.hoisted(() => vi.fn())
vi.mock('react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

import { SecurityPage } from '../../features/settings/SecurityPage'
import { useAssistantChatStore } from '../../platform/store/assistant-chat-store'

const guestStatus = { registered: false, userId: null, deviceId: 'dev-1' }
const memberStatus = { registered: true, userId: 'user-abc-123456', deviceId: 'dev-1' }

const renderPage = () => render(<SecurityPage />)

describe('SecurityPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.getAuthStatus.mockResolvedValue(guestStatus)
    mockBridge.getMeta.mockResolvedValue({})
    mockBridge.logoutAuth.mockResolvedValue({ success: true })
    // T14-4：聊天历史 store 为模块级共享态——逐用例复位
    useAssistantChatStore.setState({ messages: [] })
  })

  it('游客：身份卡显示游客态 + 登录入口，无退出/切换按钮', async () => {
    renderPage()
    await waitFor(() => expect(mockBridge.getAuthStatus).toHaveBeenCalled())
    expect(screen.getByText('游客模式')).toBeTruthy()
    expect(screen.getByText('游客模式 · 未登录')).toBeTruthy()
    expect(screen.getByTestId('security-login-button')).toBeTruthy()
    expect(screen.queryByTestId('security-logout')).toBeNull()
    expect(screen.queryByTestId('security-switch-account')).toBeNull()
    // 游客不调 auth.me
    expect(mockBridge.getMe).not.toHaveBeenCalled()
  })

  it('已登录：副行显示脱敏手机号，出现退出/切换按钮', async () => {
    mockBridge.getAuthStatus.mockResolvedValue(memberStatus)
    mockBridge.getMe.mockResolvedValue({
      id: 'u1', nickname: null, phone: '13812345678', status: 'active', timezone: 'Asia/Shanghai',
    })
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('138****5678')).toBeTruthy()
    })
    expect(screen.getByTestId('security-logout')).toBeTruthy()
    expect(screen.getByTestId('security-switch-account')).toBeTruthy()
    expect(screen.queryByTestId('security-login-button')).toBeNull()
  })

  it('已登录 + auth.me 失败：副行回退 userId 前 8 位', async () => {
    mockBridge.getAuthStatus.mockResolvedValue(memberStatus)
    mockBridge.getMe.mockRejectedValue(new BridgeError('network_error', 'offline', 'r'))
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('用户 user-abc…')).toBeTruthy()
    })
  })

  it('display_name/avatar_uri meta 存在时身份卡优先展示', async () => {
    mockBridge.getMeta.mockResolvedValue({
      display_name: '小岛', avatar_uri: 'data:image/png;base64,AAAA',
    })
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('小岛')).toBeTruthy()
    })
    const img = screen.getByAltText('头像') as HTMLImageElement
    expect(img.src).toContain('data:image/png;base64,AAAA')
  })

  // ─── 退出登录 ───

  it('退出登录：确认弹框含数据清除提示 → logoutAuth → navigate(/onboarding)', async () => {
    mockBridge.getAuthStatus.mockResolvedValue(memberStatus)
    mockBridge.getMe.mockResolvedValue({ id: 'u1', nickname: null, phone: null, status: 'active', timezone: 'UTC' })
    // T14-4（决策 #7）：登出需清空聊天历史 store（防跨账号串会话）
    useAssistantChatStore.setState({ messages: [{ id: 'm1', role: 'user', text: 'hi' }] })
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(screen.getByTestId('security-logout')).toBeTruthy())
    await user.click(screen.getByTestId('security-logout'))
    // 决策 #4：确认弹框必须写"清除本机数据；未同步记录会丢失"
    expect(screen.getByText(/退出将清除本机数据；未同步到云端的记录会丢失/)).toBeTruthy()
    await user.click(screen.getByTestId('security-confirm-ok'))
    await waitFor(() => {
      expect(mockBridge.logoutAuth).toHaveBeenCalled()
      expect(mockNavigate).toHaveBeenCalledWith('/onboarding', { replace: true })
      expect(useAssistantChatStore.getState().messages).toEqual([])
    })
  })

  it('退出确认弹框可取消，不调 logoutAuth', async () => {
    mockBridge.getAuthStatus.mockResolvedValue(memberStatus)
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(screen.getByTestId('security-logout')).toBeTruthy())
    await user.click(screen.getByTestId('security-logout'))
    await user.click(screen.getByTestId('security-confirm-cancel'))
    await waitFor(() => {
      expect(screen.queryByTestId('security-confirm-ok')).toBeNull()
    })
    expect(mockBridge.logoutAuth).not.toHaveBeenCalled()
  })

  it('logout wipe_failed：显示重试提示且不跳转', async () => {
    mockBridge.getAuthStatus.mockResolvedValue(memberStatus)
    mockBridge.logoutAuth.mockRejectedValue(new BridgeError('wipe_failed', 'db', 'r'))
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(screen.getByTestId('security-logout')).toBeTruthy())
    await user.click(screen.getByTestId('security-logout'))
    await user.click(screen.getByTestId('security-confirm-ok'))
    await waitFor(() => {
      expect(screen.getByTestId('security-error').textContent).toContain('本机数据清理失败')
    })
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('切换账号：确认后 logoutAuth → navigate(/login)', async () => {
    mockBridge.getAuthStatus.mockResolvedValue(memberStatus)
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(screen.getByTestId('security-switch-account')).toBeTruthy())
    await user.click(screen.getByTestId('security-switch-account'))
    await user.click(screen.getByTestId('security-confirm-ok'))
    await waitFor(() => {
      expect(mockBridge.logoutAuth).toHaveBeenCalled()
      expect(mockNavigate).toHaveBeenCalledWith('/login')
    })
  })

  // ─── 修改昵称 ───

  it('修改昵称：保存调 updateProfile（trim 后 payload）并刷新展示', async () => {
    mockBridge.updateProfile.mockResolvedValue({
      id: 'u1', nickname: '新名字', phone: null, status: 'guest', timezone: 'UTC',
    })
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByTestId('security-nickname-button'))
    const input = screen.getByTestId('security-nickname-input')
    await user.type(input, '  新名字 ')
    await user.click(screen.getByTestId('security-nickname-save'))
    await waitFor(() => {
      expect(mockBridge.updateProfile).toHaveBeenCalledWith('新名字')
    })
    await waitFor(() => {
      expect(screen.getByText('新名字')).toBeTruthy()
    })
  })

  it('修改昵称：纯空白时保存按钮禁用', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByTestId('security-nickname-button'))
    await user.type(screen.getByTestId('security-nickname-input'), '   ')
    expect(screen.getByTestId('security-nickname-save').hasAttribute('disabled')).toBe(true)
  })

  it('修改昵称：保存失败 → 弹层内错误行可见、弹层不关、按钮复位（REQ-31）', async () => {
    mockBridge.updateProfile.mockRejectedValue(new BridgeError('network_error', 'offline', 'r'))
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByTestId('security-nickname-button'))
    await user.type(screen.getByTestId('security-nickname-input'), '新名字')
    await user.click(screen.getByTestId('security-nickname-save'))
    // 错误必须出现在弹层内（pageError 会被遮罩盖住，不可作为唯一出口）
    await waitFor(() => {
      expect(screen.getByTestId('security-nickname-error').textContent)
        .toContain('网络错误，请检查网络连接后重试')
    })
    // 弹层保持打开可重试
    expect(screen.getByTestId('security-nickname-input')).toBeTruthy()
    // finally 复位：文案回「保存」且可再点
    const saveBtn = screen.getByTestId('security-nickname-save')
    expect(saveBtn.textContent).toBe('保存')
    expect(saveBtn.hasAttribute('disabled')).toBe(false)
    // 不再写页面级错误（保留给页面级链路）
    expect(screen.queryByTestId('security-error')).toBeNull()
  })

  // ─── 修改头像 ───

  it('修改头像：pickIcon 成功 → setMeta(avatar_uri, dataUri)', async () => {
    mockBridge.pickIcon.mockResolvedValue({ dataUri: 'data:image/jpeg;base64,/9j/AA', size: 12 })
    mockBridge.setMeta.mockResolvedValue({ success: true })
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByTestId('security-avatar-button'))
    await waitFor(() => {
      expect(mockBridge.pickIcon).toHaveBeenCalledWith('gallery')
      expect(mockBridge.setMeta).toHaveBeenCalledWith('avatar_uri', 'data:image/jpeg;base64,/9j/AA')
    })
  })

  it('修改头像：用户取消（code=cancelled）静默无报错', async () => {
    mockBridge.pickIcon.mockRejectedValue(new BridgeError('cancelled', 'user cancelled', 'r'))
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByTestId('security-avatar-button'))
    await waitFor(() => expect(mockBridge.pickIcon).toHaveBeenCalled())
    expect(mockBridge.setMeta).not.toHaveBeenCalled()
    expect(screen.queryByTestId('security-error')).toBeNull()
  })
})
