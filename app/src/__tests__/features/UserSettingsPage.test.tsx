/**
 * UserSettingsPage 组件测试 — 第十一轮 T11-6（REQ-27 / 决策 #7）。
 *
 * 覆盖（卡验收）：
 * - 恢复：meta.get(['user_profile']) 解析回填；坏 JSON/读失败回退空表单
 * - 统一保存：脏检测启用按钮 → setMeta payload（键序固定、只含非空 trim 字段）
 * - 沟通风格 chips：单选、再点取消
 * - 成功轻提示出现；保存失败错误行可见
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'

const mockBridge = vi.hoisted(() => ({
  getMeta: vi.fn(),
  setMeta: vi.fn(),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

import { UserSettingsPage } from '../../features/settings/UserSettingsPage'

const renderPage = () => render(<MemoryRouter><UserSettingsPage /></MemoryRouter>)

describe('UserSettingsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.getMeta.mockResolvedValue({})
    mockBridge.setMeta.mockResolvedValue({ success: true })
  })

  // ─── 恢复回填 ───

  it('挂载读取 user_profile 并回填各字段', async () => {
    mockBridge.getMeta.mockResolvedValue({
      user_profile: JSON.stringify({
        nickname: '老板',
        lifeRhythm: '经常加班',
        communicationStyle: '简洁直接',
      }),
    })
    renderPage()
    await waitFor(() => expect(mockBridge.getMeta).toHaveBeenCalledWith(['user_profile']))
    expect(screen.getByTestId('user-pref-nickname')).toHaveProperty('value', '老板')
    expect(screen.getByTestId('user-pref-life-rhythm')).toHaveProperty('value', '经常加班')
    expect(screen.getByTestId('user-pref-style-简洁直接')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByTestId('user-pref-style-温和鼓励')).toHaveAttribute('aria-pressed', 'false')
    // 回填后非脏：保存按钮禁用
    expect(screen.getByTestId('user-pref-save')).toHaveProperty('disabled', true)
  })

  it('坏 JSON → 空表单，不显示错误', async () => {
    mockBridge.getMeta.mockResolvedValue({ user_profile: 'not-json' })
    renderPage()
    await waitFor(() => expect(mockBridge.getMeta).toHaveBeenCalled())
    expect(screen.getByTestId('user-pref-nickname')).toHaveProperty('value', '')
    expect(screen.queryByTestId('user-pref-error')).toBeNull()
  })

  it('桥读失败 → 静默保持空表单', async () => {
    mockBridge.getMeta.mockRejectedValue(new Error('bridge down'))
    renderPage()
    await waitFor(() => expect(mockBridge.getMeta).toHaveBeenCalled())
    expect(screen.getByTestId('user-pref-occupation')).toHaveProperty('value', '')
    expect(screen.queryByTestId('user-pref-error')).toBeNull()
  })

  // ─── 统一保存 ───

  it('输入后 dirty 提示 → 保存写 meta（键序固定、只含非空字段）+ 成功轻提示', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockBridge.getMeta).toHaveBeenCalled())

    expect(screen.getByTestId('user-pref-dirty').textContent).toContain('尚未设定')
    await user.type(screen.getByTestId('user-pref-nickname'), ' 老板 ')
    await user.type(screen.getByTestId('user-pref-spending-preference'), '交通优先地铁')
    await user.click(screen.getByTestId('user-pref-style-简洁直接'))

    expect(screen.getByTestId('user-pref-dirty').textContent).toContain('有未保存的修改')
    expect(screen.getByTestId('user-pref-save')).toHaveProperty('disabled', false)
    await user.click(screen.getByTestId('user-pref-save'))

    await waitFor(() => {
      expect(mockBridge.setMeta).toHaveBeenCalledWith(
        'user_profile',
        JSON.stringify({
          nickname: '老板', // trim 后写盘
          spendingPreference: '交通优先地铁',
          communicationStyle: '简洁直接',
        }),
      )
    })
    expect(screen.getByTestId('user-pref-saved').textContent).toContain('已保存')
    // 保存成功后 dirty 清除
    expect(screen.getByTestId('user-pref-save')).toHaveProperty('disabled', true)
  })

  it('沟通风格 chip 再点一次取消选择（保存时不带该键）', async () => {
    mockBridge.getMeta.mockResolvedValue({
      user_profile: JSON.stringify({ nickname: '老板', communicationStyle: '幽默轻松' }),
    })
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('user-pref-style-幽默轻松')).toHaveAttribute('aria-pressed', 'true')
    })

    await user.click(screen.getByTestId('user-pref-style-幽默轻松'))
    expect(screen.getByTestId('user-pref-style-幽默轻松')).toHaveAttribute('aria-pressed', 'false')

    await user.click(screen.getByTestId('user-pref-save'))
    await waitFor(() => {
      expect(mockBridge.setMeta).toHaveBeenCalledWith('user_profile', JSON.stringify({ nickname: '老板' }))
    })
  })

  it('清空全部字段保存 → 写 "{}"（画像清除，注入管线回退无 context）', async () => {
    mockBridge.getMeta.mockResolvedValue({
      user_profile: JSON.stringify({ nickname: '老板' }),
    })
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('user-pref-nickname')).toHaveProperty('value', '老板')
    })
    await user.clear(screen.getByTestId('user-pref-nickname'))
    await user.click(screen.getByTestId('user-pref-save'))
    await waitFor(() => {
      expect(mockBridge.setMeta).toHaveBeenCalledWith('user_profile', '{}')
    })
  })

  it('保存失败（meta.set reject）→ 错误行可见，仍保持 dirty 可重试', async () => {
    mockBridge.setMeta.mockRejectedValue(new Error('write failed'))
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockBridge.getMeta).toHaveBeenCalled())
    await user.type(screen.getByTestId('user-pref-occupation'), '设计师')
    await user.click(screen.getByTestId('user-pref-save'))

    await waitFor(() => {
      expect(screen.getByTestId('user-pref-error').textContent).toContain('保存失败')
    })
    expect(screen.queryByTestId('user-pref-saved')).toBeNull()
    expect(screen.getByTestId('user-pref-save')).toHaveProperty('disabled', false)
  })
})
