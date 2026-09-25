/**
 * UserSettingsPage 组件测试 — 第十一轮 T11-6（REQ-27 / 决策 #7）；T19-4（REQ-74②）补同步重取。
 *
 * 覆盖（卡验收）：
 * - 恢复：meta.get(['user_profile']) 解析回填；坏 JSON/读失败回退空表单
 * - 统一保存：脏检测启用按钮 → setMeta payload（键序固定、只含非空 trim 字段）
 * - 沟通风格 chips：单选、再点取消
 * - 成功轻提示出现；保存失败错误行可见
 * - T19-4：同步完成 / 重新可见 → 静默重取；dirty 时**不覆盖输入**只给「载入最新」提示
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
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
import { useSyncStore } from '../../platform/store/sync-store'

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

// ─── T19-4 / REQ-74②：同步完成 → 重取；dirty 时不覆盖用户输入 ───

describe('UserSettingsPage 同步重取与 dirty 保护（T19-4 / REQ-74②）', () => {
  const emitSynced = (at: number) => {
    act(() => {
      useSyncStore.setState(s => ({ status: { ...s.status, state: 'idle', lastSyncedAt: at } }))
    })
  }

  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.setMeta.mockResolvedValue({ success: true })
    useSyncStore.setState(s => ({
      status: { ...s.status, state: 'idle', pendingCount: 0, lastSyncedAt: 0, failedMutations: [] },
    }))
  })

  it('同步轮完成（lastSyncedAt 前进）→ 静默重取，AI 写入的新值不切页就可见', async () => {
    useSyncStore.setState(s => ({ status: { ...s.status, lastSyncedAt: 1000 } }))
    let stored: Record<string, string | null> = {
      user_profile: JSON.stringify({ nickname: '老板' }),
    }
    mockBridge.getMeta.mockImplementation(() => Promise.resolve({ ...stored }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('user-pref-nickname')).toHaveProperty('value', '老板')
    })

    // 对话 AI 在服务端写入（sync 把权威行拉回本机）
    stored = { user_profile: JSON.stringify({ nickname: '爹', occupation: '自由职业' }) }
    emitSynced(2000)

    await waitFor(() => {
      expect(screen.getByTestId('user-pref-nickname')).toHaveProperty('value', '爹')
    })
    expect(screen.getByTestId('user-pref-occupation')).toHaveProperty('value', '自由职业')
    // 重取后非脏：这份就是当前存盘内容
    expect(screen.getByTestId('user-pref-save')).toHaveProperty('disabled', true)
    expect(screen.getByTestId('user-pref-dirty').textContent).toContain('已保存')
  })

  it('dirty 时云端更新不覆盖输入，只给提示；点「载入最新」才替换且 savedJson 同步', async () => {
    useSyncStore.setState(s => ({ status: { ...s.status, lastSyncedAt: 1000 } }))
    let stored: Record<string, string | null> = {
      user_profile: JSON.stringify({ nickname: '老板' }),
    }
    mockBridge.getMeta.mockImplementation(() => Promise.resolve({ ...stored }))
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('user-pref-nickname')).toHaveProperty('value', '老板')
    })

    // 用户正在打字（尚未保存）
    await user.type(screen.getByTestId('user-pref-extra'), '正在写的内容')

    // 与此同时 AI 改了云端画像
    stored = { user_profile: JSON.stringify({ nickname: '爹' }) }
    emitSynced(2000)

    await waitFor(() => {
      expect(screen.getByTestId('user-pref-remote-pending')).toBeInTheDocument()
    })
    // 用户的输入原样保留（既没被覆盖，也没被清空）
    expect(screen.getByTestId('user-pref-nickname')).toHaveProperty('value', '老板')
    expect(screen.getByTestId('user-pref-extra')).toHaveProperty('value', '正在写的内容')
    expect(screen.getByTestId('user-pref-dirty').textContent).toContain('有未保存的修改')

    // 用户选择「载入最新」→ 表单换成云端值，快照同步（不再脏、提示消失）
    await user.click(screen.getByTestId('user-pref-load-remote'))
    expect(screen.getByTestId('user-pref-nickname')).toHaveProperty('value', '爹')
    expect(screen.getByTestId('user-pref-extra')).toHaveProperty('value', '')
    expect(screen.queryByTestId('user-pref-remote-pending')).toBeNull()
    expect(screen.getByTestId('user-pref-dirty').textContent).toContain('已保存')
    expect(screen.getByTestId('user-pref-save')).toHaveProperty('disabled', true)
  })

  it('同步轮跑完但这个键没变（云端值 == 存盘快照）→ 不打扰：不提示也不动表单', async () => {
    useSyncStore.setState(s => ({ status: { ...s.status, lastSyncedAt: 1000 } }))
    mockBridge.getMeta.mockResolvedValue({
      user_profile: JSON.stringify({ nickname: '老板' }),
    })
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('user-pref-nickname')).toHaveProperty('value', '老板')
    })
    await user.type(screen.getByTestId('user-pref-extra'), '草稿')

    const callsBefore = mockBridge.getMeta.mock.calls.length
    emitSynced(2000)
    await waitFor(() => {
      expect(mockBridge.getMeta.mock.calls.length).toBeGreaterThan(callsBefore)
    })
    expect(screen.queryByTestId('user-pref-remote-pending')).toBeNull()
    expect(screen.getByTestId('user-pref-extra')).toHaveProperty('value', '草稿')
  })

  it('页面重新可见（visibilitychange → visible）→ 重取', async () => {
    let stored: Record<string, string | null> = {
      user_profile: JSON.stringify({ nickname: '老板' }),
    }
    mockBridge.getMeta.mockImplementation(() => Promise.resolve({ ...stored }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('user-pref-nickname')).toHaveProperty('value', '老板')
    })

    stored = { user_profile: JSON.stringify({ nickname: '爹' }) }
    act(() => { document.dispatchEvent(new Event('visibilitychange')) })
    await waitFor(() => {
      expect(screen.getByTestId('user-pref-nickname')).toHaveProperty('value', '爹')
    })
  })

  it('重取时桥读失败 → 静默保持当前视图（不报错、不清空用户已看到的字段）', async () => {
    useSyncStore.setState(s => ({ status: { ...s.status, lastSyncedAt: 1000 } }))
    mockBridge.getMeta.mockResolvedValue({
      user_profile: JSON.stringify({ nickname: '老板' }),
    })
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('user-pref-nickname')).toHaveProperty('value', '老板')
    })

    mockBridge.getMeta.mockRejectedValue(new Error('bridge down'))
    const callsBefore = mockBridge.getMeta.mock.calls.length
    emitSynced(2000)
    // 门：重取确实发生了（否则「没崩」是没跑出来的假绿）
    await waitFor(() => {
      expect(mockBridge.getMeta.mock.calls.length).toBeGreaterThan(callsBefore)
    })
    expect(screen.getByTestId('user-pref-nickname')).toHaveProperty('value', '老板')
    expect(screen.queryByTestId('user-pref-error')).toBeNull()
    expect(screen.queryByTestId('user-pref-remote-pending')).toBeNull()
  })

  it('保存成功清除待载入提示（本次保存已成为云端权威值，暂存的旧值失效）', async () => {
    useSyncStore.setState(s => ({ status: { ...s.status, lastSyncedAt: 1000 } }))
    let stored: Record<string, string | null> = {
      user_profile: JSON.stringify({ nickname: '老板' }),
    }
    mockBridge.getMeta.mockImplementation(() => Promise.resolve({ ...stored }))
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('user-pref-nickname')).toHaveProperty('value', '老板')
    })

    await user.type(screen.getByTestId('user-pref-extra'), '正在写的内容')
    stored = { user_profile: JSON.stringify({ nickname: '爹' }) }
    emitSynced(2000)
    await waitFor(() => {
      expect(screen.getByTestId('user-pref-remote-pending')).toBeInTheDocument()
    })

    await user.click(screen.getByTestId('user-pref-save'))
    await waitFor(() => {
      expect(screen.getByTestId('user-pref-saved')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('user-pref-remote-pending')).toBeNull()
  })
})
