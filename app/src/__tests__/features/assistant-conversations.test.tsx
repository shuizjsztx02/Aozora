/**
 * T-M7-03d：多会话列表与切换。
 *
 * 卡面验收落点：
 * - 新建 / 切换 / 重命名 / 删除**四个操作各有用例**
 * - 两会话消息**互不串扰**
 * - 删除当前会话后**回退到确定性目标**（列表里剩下的第一条；一条不剩 → 本地会话桶）
 * - 离线 / 未登录降级**不报错**
 *
 * 走的是集成面（AssistantPage → use-conversations → store → 桥）：会话列表的价值
 * 全在「这几层接起来对不对」，单测某一层看不出来。桥用 mock，服务端的四端点另有
 * `test_ai_conversations_api.py` 与两个 Kotlin 测试文件钉住。
 *
 * 另外两条本卡特有的边界在这里也钉住：
 * - **流式期间禁止切换**（一轮的输出落在活动会话桶上，扁平 `setMessages` 的代价）；
 * - **陈旧 id 的 404 降级**（`forgetConversation`：丢 id、留消息、下轮开新会话）
 *   ——后半段在 `use-assistant-agent-stream.test.ts` 里（那是它自然的家）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { AssistantPage } from '../../features/assistant/AssistantPage'
import { useAssistantChatStore } from '../../platform/store/assistant-chat-store'
import type { AssistantMessage } from '../../features/assistant/use-assistant'

const hookState = vi.hoisted(() => ({
  phase: 'idle' as 'idle' | 'processing' | 'executing',
  streaming: null as { text: string; reasoning: string } | null,
}))

const mockVoice = vi.hoisted(() => ({
  isRecording: false, partialText: '', finalText: '', error: null,
  startRecording: vi.fn(), stopRecording: vi.fn(), reset: vi.fn(),
}))

const mockBridge = vi.hoisted(() => ({
  listCategories: vi.fn().mockResolvedValue([]),
  listBooks: vi.fn().mockResolvedValue([{ id: 'book-1', name: '默认账本', isMaster: false }]),
  listAccounts: vi.fn().mockResolvedValue([]),
  getMeta: vi.fn().mockResolvedValue({}),
  listPersonas: vi.fn().mockResolvedValue([]),
  listSavingsGoals: vi.fn().mockResolvedValue([]),
  listBudgets: vi.fn().mockResolvedValue([]),
  triggerSync: vi.fn().mockResolvedValue(undefined),
  isAvailable: vi.fn().mockReturnValue(true),
  listConversations: vi.fn(),
  renameConversation: vi.fn(),
  deleteConversation: vi.fn(),
}))

vi.mock('../../platform/hooks/use-voice', () => ({ useVoice: () => mockVoice }))
vi.mock('../../platform/hooks/use-bridge', () => ({ useBridge: () => mockBridge }))
vi.mock('../../platform/hooks/use-permissions', () => ({
  usePermissions: () => ({
    canRecord: false,
    status: { permissions: { RECORD_AUDIO: 'not_requested' } },
    requestPermission: vi.fn().mockResolvedValue(undefined),
    openSettings: vi.fn().mockResolvedValue(undefined),
    refresh: vi.fn().mockResolvedValue(undefined),
  }),
}))
vi.mock('../../features/assistant/use-assistant', async () => {
  const { useAssistantChatStore } = await import('../../platform/store/assistant-chat-store')
  return {
    useAssistant: () => ({
      messages: useAssistantChatStore(s => s.messages),
      phase: hookState.phase,
      activeProposal: null,
      cardStatus: null,
      isSlow: false,
      pendingOp: null,
      pendingConfig: null,
      streaming: hookState.streaming,
      streamProjection: null,
      submitInput: vi.fn(),
      cancelStream: vi.fn(),
      editProposal: vi.fn(),
      undoExecution: vi.fn(),
      confirmOp: vi.fn(),
      cancelOp: vi.fn(),
      confirmConfig: vi.fn(),
      cancelConfig: vi.fn(),
      appendAssistantMessage: vi.fn(),
    }),
  }
})

const msg = (id: string, text: string): AssistantMessage => ({ id, role: 'user', text })

function serverConversation(id: string, title: string, lastActive = '2026-09-19T11:00:00Z') {
  return {
    id, title, book_id: 'book-1',
    created_at: '2026-09-18T10:00:00Z', last_active_at: lastActive,
  }
}

/** 桥错误（形状同 `BridgeError`：code 是判据）。 */
function bridgeError(code: string): Error & { code: string } {
  return Object.assign(new Error(code), { code })
}

const renderPage = () =>
  render(
    <MemoryRouter>
      <AssistantPage />
    </MemoryRouter>,
  )

/** 打开会话抽屉。 */
async function openDrawer() {
  await userEvent.click(await screen.findByTestId('conversation-list-button'))
  return screen.findByTestId('conversation-drawer')
}

describe('AssistantPage — 会话列表（T-M7-03d）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
    hookState.phase = 'idle'
    hookState.streaming = null
    Object.defineProperty(navigator, 'onLine', { value: true, configurable: true })
    mockBridge.listConversations.mockReset().mockResolvedValue({ conversations: [] })
    mockBridge.renameConversation.mockReset()
    mockBridge.deleteConversation.mockReset().mockResolvedValue({ success: true })
    useAssistantChatStore.setState({ activeConversationId: null, messages: [], projections: {} })
  })

  it('打开抽屉：列出服务端会话，当前会话高亮', async () => {
    mockBridge.listConversations.mockResolvedValue({
      conversations: [serverConversation('c-1', '午饭记账'), serverConversation('c-2', '预算调整')],
    })
    useAssistantChatStore.setState({ activeConversationId: 'c-2', messages: [] })

    renderPage()
    await openDrawer()

    expect(await screen.findByTestId('conversation-row-c-1')).toHaveTextContent('午饭记账')
    expect(screen.getByTestId('conversation-row-c-1')).toHaveAttribute('data-active', 'false')
    expect(screen.getByTestId('conversation-row-c-2')).toHaveAttribute('data-active', 'true')
    expect(mockBridge.listConversations).toHaveBeenCalledTimes(1)
  })

  it('存量会话 title 为空串 → 客户端兜底文案（0019 不回溯派生标题）', async () => {
    mockBridge.listConversations.mockResolvedValue({ conversations: [serverConversation('c-1', '')] })

    renderPage()
    await openDrawer()

    expect(await screen.findByTestId('conversation-row-c-1')).toHaveTextContent('未命名会话')
  })

  it('新建：切回本地会话桶（服务端在首轮不带 id 时自建，不需要端点）', async () => {
    mockBridge.listConversations.mockResolvedValue({ conversations: [serverConversation('c-1', '午饭记账')] })
    useAssistantChatStore.setState({ activeConversationId: 'c-1', messages: [msg('m1', '旧会话消息')] })

    renderPage()
    await openDrawer()
    await screen.findByTestId('conversation-row-c-1')

    await userEvent.click(screen.getByTestId('conversation-new'))

    expect(useAssistantChatStore.getState().activeConversationId).toBeNull()
    // 抽屉随之关闭
    await waitFor(() => expect(screen.queryByTestId('conversation-drawer')).toBeNull())
    // 旧会话的消息进了缓存桶，不丢
    expect(useAssistantChatStore.getState().projections['c-1']).toHaveLength(1)
  })

  it('切换：两会话消息互不串扰', async () => {
    mockBridge.listConversations.mockResolvedValue({
      conversations: [serverConversation('c-1', '会话一'), serverConversation('c-2', '会话二')],
    })
    useAssistantChatStore.setState({
      activeConversationId: 'c-1',
      messages: [msg('a1', '会话一的独有消息')],
      projections: { 'c-2': [msg('b1', '会话二的独有消息')] },
    })

    renderPage()
    // 当前屏是会话一
    expect(await screen.findByText('会话一的独有消息')).toBeInTheDocument()

    await openDrawer()
    await userEvent.click(await screen.findByTestId('conversation-select-c-2'))

    // 会话二的消息上屏，且**不含**会话一的任何一个字
    expect(await screen.findByText('会话二的独有消息')).toBeInTheDocument()
    expect(screen.queryByText('会话一的独有消息')).toBeNull()

    // 切回来：会话一原样
    await openDrawer()
    await userEvent.click(await screen.findByTestId('conversation-select-c-1'))
    expect(await screen.findByText('会话一的独有消息')).toBeInTheDocument()
    expect(screen.queryByText('会话二的独有消息')).toBeNull()
  })

  it('重命名：行内改名并持久化到服务端', async () => {
    mockBridge.listConversations.mockResolvedValue({ conversations: [serverConversation('c-1', '旧标题')] })
    mockBridge.renameConversation.mockResolvedValue(serverConversation('c-1', '新标题'))

    renderPage()
    await openDrawer()
    await userEvent.click(await screen.findByTestId('conversation-rename-c-1'))

    const input = screen.getByTestId('conversation-rename-input')
    await userEvent.clear(input)
    await userEvent.type(input, '新标题')
    await userEvent.click(screen.getByTestId('conversation-rename-save'))

    await waitFor(() => expect(mockBridge.renameConversation).toHaveBeenCalledWith('c-1', '新标题'))
    expect(await screen.findByTestId('conversation-row-c-1')).toHaveTextContent('新标题')
  })

  it('删除当前会话：回退到列表里剩下的第一条（确定性目标）', async () => {
    mockBridge.listConversations.mockResolvedValue({
      conversations: [serverConversation('c-1', '会话一'), serverConversation('c-2', '会话二')],
    })
    useAssistantChatStore.setState({
      activeConversationId: 'c-1',
      messages: [msg('a1', '会话一的消息')],
      projections: { 'c-2': [msg('b1', '会话二的消息')] },
    })

    renderPage()
    await openDrawer()
    await userEvent.click(await screen.findByTestId('conversation-delete-c-1'))
    // 破坏性操作必过确认
    await userEvent.click(await screen.findByTestId('conversation-delete-confirm-button-c-1'))

    await waitFor(() => expect(mockBridge.deleteConversation).toHaveBeenCalledWith('c-1'))
    // 回退目标 = 剩下那条（服务端按活跃倒序，第一条即最近活跃的另一条）
    await waitFor(() => expect(useAssistantChatStore.getState().activeConversationId).toBe('c-2'))
    // 被删会话的消息不再挂着（会话已不存在），且它的缓存被清掉
    const after = useAssistantChatStore.getState()
    expect(after.messages.map(m => m.text)).toEqual(['会话二的消息'])
    expect(after.projections['c-1']).toBeUndefined()
    // 被删的那条从列表里消失
    expect(screen.queryByTestId('conversation-row-c-1')).toBeNull()
    expect(screen.getByTestId('conversation-row-c-2')).toBeInTheDocument()
  })

  it('删除最后一条会话：回退到本地会话桶', async () => {
    mockBridge.listConversations.mockResolvedValue({ conversations: [serverConversation('c-1', '唯一会话')] })
    useAssistantChatStore.setState({ activeConversationId: 'c-1', messages: [msg('a1', '唯一会话的消息')] })

    renderPage()
    await openDrawer()
    await userEvent.click(await screen.findByTestId('conversation-delete-c-1'))
    await userEvent.click(await screen.findByTestId('conversation-delete-confirm-button-c-1'))

    await waitFor(() => expect(useAssistantChatStore.getState().activeConversationId).toBeNull())
    expect(useAssistantChatStore.getState().messages).toEqual([])
  })

  it('删除时服务端 404：与成功同义，不报错也不弹红条', async () => {
    mockBridge.listConversations.mockResolvedValue({ conversations: [serverConversation('c-1', '会话一')] })
    mockBridge.deleteConversation.mockRejectedValue(bridgeError('not_found'))
    useAssistantChatStore.setState({ activeConversationId: 'c-1', messages: [msg('a1', 'x')] })

    renderPage()
    await openDrawer()
    await userEvent.click(await screen.findByTestId('conversation-delete-c-1'))
    await userEvent.click(await screen.findByTestId('conversation-delete-confirm-button-c-1'))

    // 本地照常清理（「这个 id 不是我的会话了」），且没有错误条
    await waitFor(() => expect(useAssistantChatStore.getState().activeConversationId).toBeNull())
    expect(screen.queryByTestId('conversation-error')).toBeNull()
  })

  it('删除失败（网络）：可见提示 + 不误删本地会话', async () => {
    mockBridge.listConversations.mockResolvedValue({ conversations: [serverConversation('c-1', '会话一')] })
    mockBridge.deleteConversation.mockRejectedValue(bridgeError('network'))
    useAssistantChatStore.setState({ activeConversationId: 'c-1', messages: [msg('a1', 'x')] })

    renderPage()
    await openDrawer()
    await userEvent.click(await screen.findByTestId('conversation-delete-c-1'))
    await userEvent.click(await screen.findByTestId('conversation-delete-confirm-button-c-1'))

    expect(await screen.findByTestId('conversation-error')).toHaveTextContent('网络异常，请稍后重试')
    // 没删成 → 本地不动
    expect(useAssistantChatStore.getState().activeConversationId).toBe('c-1')
  })

  it('离线降级：只读本机缓存、禁用新建、不报错', async () => {
    Object.defineProperty(navigator, 'onLine', { value: false, configurable: true })
    useAssistantChatStore.setState({
      activeConversationId: null,
      messages: [msg('l1', '离线记的一笔')],
      projections: { 'c-9': [msg('c9', '别的会话')] },
    })

    renderPage()
    await openDrawer()

    expect(await screen.findByTestId('conversation-hint')).toHaveTextContent('离线中，仅显示本机缓存')
    // 本机缓存的桶照常可读可切
    expect(await screen.findByTestId('conversation-row-c-9')).toHaveTextContent('别的会话')
    expect(screen.getByTestId('conversation-row-local')).toHaveAttribute('data-active', 'true')
    // 新建与写操作禁用
    expect(screen.getByTestId('conversation-new')).toBeDisabled()
    expect(screen.getByTestId('conversation-rename-c-9')).toBeDisabled()
    expect(screen.getByTestId('conversation-delete-c-9')).toBeDisabled()
    // **不报错**：降级不是错误路径，且没有打服务端
    expect(screen.queryByTestId('conversation-error')).toBeNull()
    expect(mockBridge.listConversations).not.toHaveBeenCalled()
  })

  it('未登录降级（桥回 auth_lost）：提示未登录而不是报错', async () => {
    mockBridge.listConversations.mockRejectedValue(bridgeError('auth_lost'))

    renderPage()
    await openDrawer()

    expect(await screen.findByTestId('conversation-hint')).toHaveTextContent('未登录，仅显示本机缓存')
    expect(screen.queryByTestId('conversation-error')).toBeNull()
    expect(screen.getByTestId('conversation-new')).toBeDisabled()
  })

  it('桥不具备会话命令（旧 APK）：整块降级，不炸页面', async () => {
    const saved = mockBridge.listConversations
    // @ts-expect-error 故意模拟旧 APK 的命令表
    mockBridge.listConversations = undefined
    try {
      renderPage()
      await openDrawer()
      expect(await screen.findByTestId('conversation-hint'))
        .toHaveTextContent('当前版本不支持会话列表，仅显示本机缓存')
    } finally {
      mockBridge.listConversations = saved
    }
  })

  it('流式期间：切换与新建禁用，给一行说明', async () => {
    mockBridge.listConversations.mockResolvedValue({ conversations: [serverConversation('c-1', '会话一')] })
    hookState.streaming = { text: '正在回答', reasoning: '' }
    hookState.phase = 'processing'

    renderPage()
    await openDrawer()

    expect(await screen.findByTestId('conversation-hint'))
      .toHaveTextContent('本轮进行中，结束后可切换会话')
    expect(screen.getByTestId('conversation-new')).toBeDisabled()
    expect(screen.getByTestId('conversation-select-c-1')).toBeDisabled()
    expect(screen.getByTestId('conversation-rename-c-1')).toBeDisabled()
    expect(screen.getByTestId('conversation-delete-c-1')).toBeDisabled()
  })
})

describe('assistant-chat-store — 03d 的两个会话动作', () => {
  beforeEach(() => {
    useAssistantChatStore.setState({ activeConversationId: null, messages: [], projections: {} })
  })

  it('forgetConversation：丢 id、**不动消息**、下一轮开新会话', () => {
    useAssistantChatStore.setState({
      activeConversationId: 'c-stale',
      messages: [msg('m1', '半截内容')],
      projections: { 'c-other': [msg('o1', '别的')] },
    })

    useAssistantChatStore.getState().forgetConversation()

    const s = useAssistantChatStore.getState()
    expect(s.activeConversationId).toBeNull()
    // 关键：这一屏的消息留在原地（用户刚看见的内容不该凭空消失或被本地桶顶掉）
    expect(s.messages.map(m => m.text)).toEqual(['半截内容'])
    expect(s.projections['c-stale']).toBeUndefined()
    // 别的会话的缓存不受影响
    expect(s.projections['c-other']).toHaveLength(1)
  })

  it('forgetConversation：本来就在本地会话桶时是空操作', () => {
    useAssistantChatStore.setState({ activeConversationId: null, messages: [msg('m1', 'x')] })
    const before = useAssistantChatStore.getState().messages

    useAssistantChatStore.getState().forgetConversation()

    expect(useAssistantChatStore.getState().messages).toBe(before)
  })

  it('removeConversation：删的不是当前会话 → 只清它的缓存', () => {
    useAssistantChatStore.setState({
      activeConversationId: 'c-1',
      messages: [msg('a1', '当前')],
      projections: { 'c-2': [msg('b1', '别的')] },
    })

    useAssistantChatStore.getState().removeConversation('c-2', 'c-1')

    const s = useAssistantChatStore.getState()
    expect(s.activeConversationId).toBe('c-1')
    expect(s.messages.map(m => m.text)).toEqual(['当前'])
    expect(s.projections['c-2']).toBeUndefined()
  })
})