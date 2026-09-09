/**
 * LoginPage 组件测试 — 第十轮 T10-3（REQ-19）。
 *
 * 覆盖（卡验收）：登录成功链 loginAuth → triggerAuthSync → navigate('/home')；
 * 验证码错误（invalid_code）不跳转并提示；dev 验证码提示渲染。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BridgeError } from '../../platform/bridge/bridge-types'

const mockBridge = vi.hoisted(() => ({
  getAuthStatus: vi.fn(),
  loginAuth: vi.fn(),
  triggerAuthSync: vi.fn(),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

const mockNavigate = vi.hoisted(() => vi.fn())
vi.mock('react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

import { LoginPage } from '../../features/auth/LoginPage'

async function fillPhoneAndCode(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByTestId('phone-input'), '13800001111')
  await user.click(screen.getByText('获取验证码'))
  await user.type(screen.getByTestId('code-input'), '000000')
}

describe('LoginPage — T10-3 真登录链', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.getAuthStatus.mockResolvedValue({ registered: false, userId: null, deviceId: 'd' })
    mockBridge.loginAuth.mockResolvedValue({ userId: 'u-9', isNewUser: true })
    mockBridge.triggerAuthSync.mockResolvedValue({ outcome: 'SUCCESS' })
  })

  it('渲染 dev 固定验证码提示', () => {
    render(<LoginPage />)
    expect(screen.getByTestId('dev-code-hint').textContent).toContain('000000')
  })

  it('登录成功：loginAuth(phone, code) → triggerAuthSync → navigate(/home)', async () => {
    const user = userEvent.setup()
    render(<LoginPage />)
    await fillPhoneAndCode(user)
    await user.click(screen.getByRole('button', { name: '登录' }))
    await waitFor(() => {
      expect(mockBridge.loginAuth).toHaveBeenCalledWith('13800001111', '000000')
    })
    await waitFor(() => {
      expect(mockBridge.triggerAuthSync).toHaveBeenCalled()
      expect(mockNavigate).toHaveBeenCalledWith('/home', { replace: true })
    })
  })

  it('验证码错误（invalid_code）：提示且不跳转', async () => {
    mockBridge.loginAuth.mockRejectedValue(new BridgeError('invalid_code', '验证码不正确或已过期', 'r1'))
    const user = userEvent.setup()
    render(<LoginPage />)
    await fillPhoneAndCode(user)
    await user.click(screen.getByRole('button', { name: '登录' }))
    await waitFor(() => {
      expect(screen.getByText('验证码不正确或已过期，请重试')).toBeTruthy()
    })
    expect(mockBridge.triggerAuthSync).not.toHaveBeenCalled()
    expect(mockNavigate).not.toHaveBeenCalledWith('/home', { replace: true })
  })

  it('同步失败不阻断跳转（登录已成功，进首页后再同步）', async () => {
    mockBridge.triggerAuthSync.mockRejectedValue(new BridgeError('network_error', 'offline', 'r2'))
    const user = userEvent.setup()
    render(<LoginPage />)
    await fillPhoneAndCode(user)
    await user.click(screen.getByRole('button', { name: '登录' }))
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/home', { replace: true })
    })
  })
})
