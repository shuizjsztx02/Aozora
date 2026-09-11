/**
 * T-M4-04b: AssistantPage 语音集成测试。
 *
 * 验证：
 * - 麦克风按钮渲染 + 状态切换（录音中/非录音）
 * - 实时部分转写显示
 * - 最终转写文本填入输入框
 * - 语音错误显示（T12-1：错误码映射中文）
 * - 完整链路：录音 → 转写 → 填入输入 → 发送
 * - T12-1（REQ-29 / 决策 #8）：按需授权流——未授权点 mic 请求权限、
 *   granted 续链录音、denied 红条+「授权」、permanently_denied「去设置」
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { AssistantPage } from '../../features/assistant/AssistantPage'
import type { AssistantPhase } from '../../features/assistant/use-assistant'

// ─── Mocks ───

const mockVoice = vi.hoisted(() => ({
  isRecording: false,
  partialText: '',
  finalText: '',
  error: null as { code: string; message: string } | null,
  startRecording: vi.fn(),
  stopRecording: vi.fn(),
  reset: vi.fn(),
}))

// T12-1：usePermissions mock（默认已授予，单用例内改写 status/canRecord 后 rerender 驱动续链）
const mockPermissions = vi.hoisted(() => ({
  canRecord: true,
  loading: false,
  status: {
    permissions: {
      RECORD_AUDIO: 'granted',
      CAMERA: 'not_requested',
      READ_MEDIA_IMAGES: 'not_requested',
      POST_NOTIFICATIONS: 'not_requested',
    } as Record<string, string>,
    canRecordAudio: true,
    canUseCamera: false,
    canAccessImages: false,
  },
  canCamera: false,
  canGallery: false,
  shouldGuideSettings: false,
  requestPermission: vi.fn().mockResolvedValue(undefined),
  openSettings: vi.fn().mockResolvedValue(undefined),
  // T14-6：refresh 现返回查询结果（PermissionStatusData）供恢复流判定
  refresh: vi.fn(async () => mockPermissions.status),
}))

/** 改写 RECORD_AUDIO 状态（模拟 onPermissionResult 推送后 usePermissions 刷新） */
const setRecordStatus = (s: string) => {
  mockPermissions.status.permissions.RECORD_AUDIO = s
  mockPermissions.canRecord = s === 'granted'
  mockPermissions.status.canRecordAudio = s === 'granted'
}

const mockBridge = vi.hoisted(() => ({
  listCategories: vi.fn().mockResolvedValue([]),
  listBooks: vi.fn().mockResolvedValue([{ id: 'book-1', name: '默认账本' }]),
  // T9-7（REQ-15）：账户列表 mock（默认无账户，选择器传 null）
  listAccounts: vi.fn().mockResolvedValue([]),
  // T9-5b（REQ-11）：meta.get mock（默认无默认选择）
  getMeta: vi.fn().mockResolvedValue({}),
  // T11-7（REQ-28）：角色列表 mock（默认无角色 → header 无角色名）
  listPersonas: vi.fn().mockResolvedValue([]),
  // T13-5（REQ-48）：现有对象名单装载（context 注入 + config 解析数据源）
  listSavingsGoals: vi.fn().mockResolvedValue([]),
  listBudgets: vi.fn().mockResolvedValue([]),
  insertTransaction: vi.fn().mockResolvedValue({ id: 'tx-1' }),
  triggerSync: vi.fn().mockResolvedValue(undefined),
  interpretAI: vi.fn().mockResolvedValue(null),
  validateProposal: vi.fn().mockResolvedValue({ valid: true }),
  executeProposal: vi.fn().mockResolvedValue({ transaction_ids: [], undo_window_seconds: 5 }),
  softDeleteTransaction: vi.fn().mockResolvedValue(undefined),
  isAvailable: vi.fn().mockReturnValue(false),
}))

const mockAssistant = vi.hoisted(() => ({
  messages: [] as unknown[],
  phase: 'idle' as AssistantPhase,
  activeProposal: null,
  cardStatus: null,
  isSlow: false,
  submitInput: vi.fn(),
  editProposal: vi.fn(),
  undoExecution: vi.fn(),
  // T9-8（REQ-16）：页面解构此方法（图片批量链路完成后追加消息）
  appendAssistantMessage: vi.fn(),
}))

vi.mock('../../platform/hooks/use-voice', () => ({
  useVoice: () => mockVoice,
}))

vi.mock('../../platform/hooks/use-permissions', () => ({
  usePermissions: () => mockPermissions,
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

vi.mock('../../features/assistant/use-assistant', () => ({
  useAssistant: () => mockAssistant,
}))

const renderPage = () =>
  render(
    <MemoryRouter>
      <AssistantPage />
    </MemoryRouter>
  )

describe('AssistantPage — 语音集成 (T-M4-04b)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // jsdom 没有 scrollTo，mock 之
    Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
    // 默认非录音状态
    mockVoice.isRecording = false
    mockVoice.partialText = ''
    mockVoice.finalText = ''
    mockVoice.error = null
    mockVoice.startRecording.mockResolvedValue(true)
    mockVoice.stopRecording.mockResolvedValue('')
    mockVoice.reset.mockImplementation(() => {
      mockVoice.partialText = ''
      mockVoice.finalText = ''
    })
    mockAssistant.phase = 'idle'
    mockAssistant.messages = []
    // T12-1：每例默认已授予（个别用例内改写）
    setRecordStatus('granted')
  })

  it('渲染麦克风按钮', () => {
    renderPage()
    const mic = screen.getByTestId('voice-mic-button')
    expect(mic).toBeInTheDocument()
    expect(mic).toHaveAttribute('aria-label', '语音输入')
  })

  it('点击麦克风按钮调用 startRecording', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByTestId('voice-mic-button'))
    expect(mockVoice.startRecording).toHaveBeenCalledTimes(1)
  })

  it('录音中时麦克风按钮切换为停止状态', () => {
    mockVoice.isRecording = true
    renderPage()
    const mic = screen.getByTestId('voice-mic-button')
    expect(mic).toHaveAttribute('aria-label', '停止录音')
    // 输入框 placeholder 变为「正在聆听…」
    expect(screen.getByPlaceholderText('正在聆听…')).toBeInTheDocument()
  })

  it('录音中点击麦克风调用 stopRecording', async () => {
    mockVoice.isRecording = true
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByTestId('voice-mic-button'))
    expect(mockVoice.stopRecording).toHaveBeenCalledTimes(1)
  })

  it('录音中显示实时部分转写文本', () => {
    mockVoice.isRecording = true
    mockVoice.partialText = '午饭十五'
    renderPage()
    const partial = screen.getByTestId('voice-partial')
    expect(partial).toBeInTheDocument()
    expect(partial.textContent).toContain('午饭十五')
  })

  it('没有部分转写文本时不显示部分转写条', () => {
    mockVoice.isRecording = true
    mockVoice.partialText = ''
    renderPage()
    expect(screen.queryByTestId('voice-partial')).not.toBeInTheDocument()
  })

  it('permission_denied 错误走恢复条而非英文红条（T14-6：refresh 后内联「授权」）', async () => {
    setRecordStatus('denied')
    mockVoice.error = { code: 'permission_denied', message: 'RECORD_AUDIO permission not granted' }
    renderPage()
    // 普通红条被抑制，恢复条（授权钮）出现
    await waitFor(() => expect(screen.getByTestId('voice-permission-bar')).toBeInTheDocument())
    expect(screen.queryByTestId('voice-error')).toBeNull()
    expect(mockPermissions.refresh).toHaveBeenCalled()
    expect(screen.getByTestId('voice-permission-bar').textContent).not.toContain('RECORD_AUDIO')
  })

  it('code 丢失时按英文 message 反推 permission_denied 并出恢复条（T14-6）', async () => {
    mockVoice.error = { message: 'RECORD_AUDIO permission not granted' } as never
    renderPage()
    await waitFor(() => expect(screen.getByTestId('voice-permission-bar')).toBeInTheDocument())
    expect(screen.queryByTestId('voice-error')).toBeNull()
    expect(document.body.textContent).not.toContain('permission not granted')
  })

  it('未知错误码不再透出原生 message（T14-6：中文兜底）', () => {
    mockVoice.error = { code: 'some_new_code', message: 'unexpected internal state' }
    renderPage()
    const err = screen.getByTestId('voice-error')
    expect(err.textContent).toBe('录音操作未成功，请重试')
  })

  it('转写完成后将文本填入输入框', async () => {
    // 首次渲染：finalText 为空
    const { rerender } = renderPage()
    expect(screen.getByPlaceholderText('输入记账短句…')).toHaveValue('')

    // 模拟转写完成：finalText 更新
    mockVoice.finalText = '午饭 12.5'
    rerender(
      <MemoryRouter>
        <AssistantPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByPlaceholderText('输入记账短句…')).toHaveValue('午饭 12.5')
    })
    expect(mockVoice.reset).toHaveBeenCalled()
  })

  it('完整链路：转写填入后点击发送调用 submitInput', async () => {
    mockVoice.finalText = '晚饭 25'
    const user = userEvent.setup()
    renderPage()

    // 等待转写填入输入框
    await waitFor(() => {
      expect(screen.getByPlaceholderText('输入记账短句…')).toHaveValue('晚饭 25')
    })

    // 点击发送
    await user.click(screen.getByText('发送'))
    expect(mockAssistant.submitInput).toHaveBeenCalledWith('晚饭 25')
  })

  it('处理/执行阶段禁用麦克风按钮', () => {
    mockAssistant.phase = 'processing'
    renderPage()
    expect(screen.getByTestId('voice-mic-button')).toBeDisabled()
  })

  // ─── 第十一轮 T11-7（REQ-28）：header 角色名 + 角色管理入口 ───

  it('当前角色渲染在 header，右上有角色管理入口', async () => {
    mockBridge.getMeta.mockResolvedValueOnce({ active_persona_id: 'p-2' })
    mockBridge.listPersonas.mockResolvedValueOnce([
      { id: 'p-1', name: '小贝', avatar: '😊', persona: '温柔搭子', createdAt: 1, updatedAt: 1, version: 1 },
      { id: 'p-2', name: '饭团', avatar: '😋', persona: '吃货伙伴', createdAt: 2, updatedAt: 2, version: 1 },
    ])
    renderPage()
    await waitFor(() => expect(screen.getByText('饭团')).toBeInTheDocument())
    const manage = screen.getByTestId('persona-manage-button')
    expect(manage).toHaveAttribute('aria-label', '角色管理')
  })

  it('active 指向已删角色 → 回退"小贝"名字', async () => {
    mockBridge.getMeta.mockResolvedValueOnce({ active_persona_id: 'gone' })
    mockBridge.listPersonas.mockResolvedValueOnce([
      { id: 'p-1', name: '小贝', avatar: null, persona: '温柔搭子', createdAt: 1, updatedAt: 1, version: 1 },
    ])
    renderPage()
    await waitFor(() => expect(screen.getByText('小贝')).toBeInTheDocument())
  })

  // ─── 第十三轮 T13-6（REQ-49）：右上文字钮 → 24px 当前角色头像圆钮 ───
  // 注：activePersona 异步装载会整块替换 header 的 right 子树（节点失连），
  // 必须 waitFor 内重查 getByTestId，不能先捕获节点再轮询。

  it('头像钮渲染当前角色 emoji 头像（无 img），点跳转不炸且 testid/aria 保留', async () => {
    mockBridge.getMeta.mockResolvedValueOnce({ active_persona_id: 'p-2' })
    mockBridge.listPersonas.mockResolvedValueOnce([
      { id: 'p-1', name: '小贝', avatar: '😊', persona: '温柔搭子', createdAt: 1, updatedAt: 1, version: 1 },
      { id: 'p-2', name: '饭团', avatar: '😋', persona: '吃货伙伴', createdAt: 2, updatedAt: 2, version: 1 },
    ])
    const user = userEvent.setup()
    renderPage()
    await waitFor(() =>
      expect(screen.getByTestId('persona-manage-button')).toHaveTextContent('😋'))
    const manage = screen.getByTestId('persona-manage-button')
    expect(manage.querySelector('img')).toBeNull()
    expect(manage).toHaveAttribute('aria-label', '角色管理')
    await user.click(manage)
    expect(screen.getByTestId('persona-manage-button')).toBeInTheDocument()
  })

  it('base64 头像 → CategoryIcon img 圆图分支', async () => {
    const dataUrl = 'data:image/webp;base64,UklGRgA='
    mockBridge.getMeta.mockResolvedValueOnce({ active_persona_id: 'p-1' })
    mockBridge.listPersonas.mockResolvedValueOnce([
      { id: 'p-1', name: '自定义', avatar: dataUrl, persona: 'x', createdAt: 1, updatedAt: 1, version: 1 },
    ])
    renderPage()
    await waitFor(() =>
      expect(screen.getByTestId('persona-manage-button')
        .querySelector('img')).toHaveAttribute('src', dataUrl))
  })

  it('未选角色/无头像 → 👤 兜底', async () => {
    renderPage()
    await waitFor(() =>
      expect(screen.getByTestId('persona-manage-button')).toHaveTextContent('👤'))
  })

  // ─── 第十二轮 T12-1（REQ-29 / 决策 #8）：麦克风按需授权流 ───

  it('未授权时点麦克风 → 调 requestPermission，不调 startRecording', async () => {
    setRecordStatus('not_requested')
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByTestId('voice-mic-button'))
    expect(mockPermissions.requestPermission).toHaveBeenCalledWith('RECORD_AUDIO')
    expect(mockVoice.startRecording).not.toHaveBeenCalled()
  })

  it('授权 granted 后自动续链开始录音，红条不出现', async () => {
    setRecordStatus('not_requested')
    const user = userEvent.setup()
    const { rerender } = renderPage()
    await user.click(screen.getByTestId('voice-mic-button'))
    expect(mockVoice.startRecording).not.toHaveBeenCalled()

    // 模拟系统弹框「允许」→ onPermissionResult 推送 → usePermissions 刷新 status
    setRecordStatus('granted')
    rerender(
      <MemoryRouter>
        <AssistantPage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(mockVoice.startRecording).toHaveBeenCalledTimes(1))
    expect(screen.queryByTestId('voice-permission-bar')).not.toBeInTheDocument()
  })

  it('denied → 中文红条 + 内联「授权」钮重发请求', async () => {
    setRecordStatus('not_requested')
    const user = userEvent.setup()
    const { rerender } = renderPage()
    await user.click(screen.getByTestId('voice-mic-button'))

    setRecordStatus('denied')
    rerender(
      <MemoryRouter>
        <AssistantPage />
      </MemoryRouter>,
    )
    const bar = await screen.findByTestId('voice-permission-bar')
    expect(bar.textContent).toContain('未获得麦克风权限')
    expect(mockVoice.startRecording).not.toHaveBeenCalled()

    await user.click(screen.getByTestId('voice-permission-grant'))
    expect(mockPermissions.requestPermission).toHaveBeenCalledTimes(2)
  })

  it('permanently_denied → 点麦克风直接显「去设置」引导，不重发系统请求', async () => {
    setRecordStatus('permanently_denied')
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByTestId('voice-mic-button'))
    expect(mockPermissions.requestPermission).not.toHaveBeenCalled()

    const bar = await screen.findByTestId('voice-permission-bar')
    expect(bar.textContent).toContain('系统设置')
    await user.click(screen.getByTestId('voice-permission-settings'))
    expect(mockPermissions.openSettings).toHaveBeenCalledTimes(1)
  })
})
