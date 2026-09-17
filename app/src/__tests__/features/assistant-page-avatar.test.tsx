/**
 * T16-4（REQ-60）：聊天消息头像测试。
 *
 * 验证：
 * - 助手/用户两条消息各渲一个头像，两列布局（助手左、用户右，头像与气泡同行）
 * - 助手头像 = 当前角色头像（activePersona.avatar）、用户头像 = meta['avatar_uri']
 *   （由已有 getMeta 批次顺路读，零新增桥命令）
 * - 无头像时两侧均「👤」兜底；data URI 走 CategoryIcon 的 <img> 圆形分支
 * - 历史账单卡仍渲染在消息行下（T16-2 布局不回归）
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { AssistantPage } from '../../features/assistant/AssistantPage'
import { useAssistantChatStore } from '../../platform/store/assistant-chat-store'
import type { AssistantMessage } from '../../features/assistant/use-assistant'
import type { ActionProposal } from '../../domain/action-proposal'
import type { PersonaData } from '../../platform/bridge/bridge-types'

const mockVoice = vi.hoisted(() => ({
  isRecording: false,
  partialText: '',
  finalText: '',
  error: null,
  startRecording: vi.fn(),
  stopRecording: vi.fn(),
  reset: vi.fn(),
}))

const mockBridge = vi.hoisted(() => ({
  listCategories: vi.fn().mockResolvedValue([
    { id: 'cat-lunch', kind: 'expense', name: '午饭', parentId: null, icon: '🥟', version: 1 },
  ]),
  listBooks: vi.fn().mockResolvedValue([{ id: 'book-1', name: '默认账本', isMaster: false }]),
  listAccounts: vi.fn().mockResolvedValue([]),
  getMeta: vi.fn().mockResolvedValue({}),
  listPersonas: vi.fn().mockResolvedValue([]),
  listSavingsGoals: vi.fn().mockResolvedValue([]),
  listBudgets: vi.fn().mockResolvedValue([]),
  triggerSync: vi.fn().mockResolvedValue(undefined),
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
      phase: 'idle',
      activeProposal: null,
      cardStatus: null,
      isSlow: false,
      pendingOp: null,
      pendingConfig: null,
      submitInput: vi.fn(),
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

const persona = (avatar: string | null): PersonaData => ({
  id: 'p-1', name: '小贝', avatar, persona: '', createdAt: 0, updatedAt: 0, version: 1,
})

const userMsg = (id: string, text: string): AssistantMessage => ({ id, role: 'user', text })
const assistantMsg = (id: string, text: string): AssistantMessage => ({ id, role: 'assistant', text })

const cardProposal = (proposalId: string): ActionProposal => ({
  proposalId,
  action: 'add_tx',
  confidence: 0.9,
  requiresConfirmation: false,
  message: '记下这笔',
  warnings: [],
  transactions: [{
    type: 'expense',
    amountCents: 4500,
    categoryId: 'cat-lunch',
    bizDate: '2026-09-17',
    note: '午饭',
  }],
})

const renderPage = () =>
  render(
    <MemoryRouter>
      <AssistantPage />
    </MemoryRouter>,
  )

describe('AssistantPage — 消息头像（T16-4 / REQ-60）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
    // clearAllMocks 不清实现，逐用例复位头像数据源（其余 mock 为常量实现无需复位）
    mockBridge.listPersonas.mockResolvedValue([])
    mockBridge.getMeta.mockResolvedValue({})
    useAssistantChatStore.setState({ messages: [] })
  })

  it('助手/用户消息各渲头像：角色头像 + meta.avatar_uri，与气泡同行两列', async () => {
    mockBridge.listPersonas.mockResolvedValue([persona('🐱')])
    mockBridge.getMeta.mockResolvedValue({ avatar_uri: 'data:image/png;base64,AAAA' })
    useAssistantChatStore.setState({
      messages: [userMsg('msg-u', '午饭 45'), assistantMsg('msg-a', '已记录 1 笔交易')],
    })
    renderPage()

    const assistantAvatar = screen.getByTestId('msg-avatar-assistant')
    const userAvatar = screen.getByTestId('msg-avatar-user')

    // 角色头像（emoji 分支）；用户头像（data URI → <img> 圆形分支）
    await waitFor(() => expect(assistantAvatar.textContent).toBe('🐱'))
    await waitFor(() => expect(userAvatar.querySelector('img')).not.toBeNull())
    expect(assistantAvatar.querySelector('img')).toBeNull()
    expect(userAvatar.querySelector('img')).toHaveAttribute('src', 'data:image/png;base64,AAAA')

    // 圆形带玻璃外框（两列布局：助手左、用户右）
    expect(assistantAvatar.className).toContain('rounded-full')
    expect(assistantAvatar.style.border).toContain('var(--glass-border)')
    expect(assistantAvatar.parentElement?.className).not.toContain('flex-row-reverse')
    expect(userAvatar.parentElement?.className).toContain('flex-row-reverse')

    // 头像与气泡同一行：头像所在行的文本即气泡文本
    expect(assistantAvatar.parentElement?.textContent).toContain('已记录 1 笔交易')
    expect(userAvatar.parentElement?.textContent).toContain('午饭 45')

    // 零新增桥命令：头像仍由既有 getMeta 批次顺路读
    expect(mockBridge.getMeta).toHaveBeenCalledWith(expect.arrayContaining(['avatar_uri']))
  })

  it('未设角色/用户头像时两侧均 👤 兜底（不成 img 分支）', async () => {
    useAssistantChatStore.setState({
      messages: [userMsg('msg-u', '午饭 45'), assistantMsg('msg-a', '收到')],
    })
    renderPage()

    await waitFor(() => expect(screen.getAllByTestId(/^msg-avatar-/)).toHaveLength(2))
    expect(screen.getByTestId('msg-avatar-assistant').textContent).toBe('👤')
    expect(screen.getByTestId('msg-avatar-user').textContent).toBe('👤')
    expect(screen.getByTestId('msg-avatar-user').querySelector('img')).toBeNull()
  })

  it('带 proposal 的消息：头像与历史账单卡并存（T16-2 布局不回归）', async () => {
    useAssistantChatStore.setState({
      messages: [
        userMsg('msg-u', '午饭 45'),
        {
          ...assistantMsg('msg-a', '已记录 1 笔交易'),
          proposal: cardProposal('prop-1'),
          proposalStatus: 'committed',
        },
      ],
    })
    renderPage()

    expect(screen.getByTestId('msg-avatar-assistant')).toBeInTheDocument()
    expect(screen.getByTestId('msg-avatar-user')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('history-card')).toBeInTheDocument())
    expect(screen.getByTestId('card-status').textContent).toContain('已记录')
  })
})