/**
 * T16-2（REQ-58）：聊天历史账单卡常驻测试。
 *
 * 验证：
 * - 带 proposal 的消息在气泡下渲染只读账单卡；切页 remount（messages 出自持久化
 *   store，页面侧只重挂载）后仍在，且无编辑/撤销钮
 * - 当轮活动卡与历史卡不重复出卡：同提案只渲可编辑的活动卡；新记一笔后上一张卡
 *   转为历史卡常驻
 * - proposalStatus 语义：undone=已撤销（灰化）、error=记账失败；无 proposal /
 *   transactions 为空不成卡
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, act, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { AssistantPage } from '../../features/assistant/AssistantPage'
import { useAssistantChatStore } from '../../platform/store/assistant-chat-store'
import type { AssistantMessage } from '../../features/assistant/use-assistant'
import type { ActionProposal } from '../../domain/action-proposal'

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

/** 页面侧 useAssistant 打桩：messages 接真实 persist store（remount 恢复即真实链路），
 *  活动卡/卡态等瞬时态由用例按需设置。 */
const mockAssistantState = vi.hoisted(() => ({
  activeProposal: null as ActionProposal | null,
  cardStatus: null as 'executing' | 'committed' | 'error' | 'pending-category' | null,
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
      activeProposal: mockAssistantState.activeProposal,
      cardStatus: mockAssistantState.cardStatus,
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

function proposal(proposalId: string): ActionProposal {
  return {
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
      bizDate: '2026-09-16',
      note: '午饭',
    }],
  }
}

const cardMsg = (
  id: string,
  proposalId: string,
  proposalStatus: AssistantMessage['proposalStatus'],
  text = '已记录 1 笔交易',
): AssistantMessage => ({
  id, role: 'assistant', text, proposal: proposal(proposalId), proposalStatus,
})

const renderPage = () =>
  render(
    <MemoryRouter>
      <AssistantPage />
    </MemoryRouter>,
  )

describe('AssistantPage — 历史账单卡常驻（T16-2 / REQ-58）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
    mockAssistantState.activeProposal = null
    mockAssistantState.cardStatus = null
    useAssistantChatStore.setState({ messages: [] })
  })

  it('带 proposal 的历史消息渲染只读卡；切页 remount 后仍在、无编辑/撤销钮', async () => {
    useAssistantChatStore.setState({ messages: [cardMsg('msg-1', 'prop-1', 'committed')] })
    const { unmount } = renderPage()
    await waitFor(() => expect(screen.getByTestId('card-cat-label').textContent).toBe('午饭'))
    expect(screen.getByTestId('card-amount-display').textContent).toContain('45.00')
    expect(screen.getByTestId('card-view-date').textContent).toBe('2026-09-16')
    expect(screen.getByTestId('card-view-note').textContent).toBe('午饭')
    expect(screen.getByTestId('card-book-name').textContent).toBe('默认账本')
    expect(screen.getByTestId('card-status').textContent).toContain('已记录')
    // 提案未点名账户 + 无默认账户 → 「不使用账户」（与活动卡同口径）
    expect(screen.getByTestId('card-account-chip').textContent).toContain('不使用账户')
    // 历史卡只读：编辑/撤销钮不存在，活动卡也不存在（activeProposal 为内存态，恢复后为空）
    expect(screen.queryByTestId('card-edit')).toBeNull()
    expect(screen.queryByTestId('card-undo')).toBeNull()
    expect(screen.queryByTestId('confirmation-card')).toBeNull()

    unmount()
    renderPage()
    expect(screen.getByTestId('history-card')).toBeInTheDocument()
    expect(screen.getByTestId('card-amount-display').textContent).toContain('45.00')
    expect(screen.queryByTestId('card-edit')).toBeNull()
  })

  it('同提案不重复出卡：当轮活动卡仍可编辑/撤销，历史卡不渲染', () => {
    useAssistantChatStore.setState({ messages: [cardMsg('msg-1', 'prop-1', 'committed')] })
    mockAssistantState.activeProposal = proposal('prop-1')
    mockAssistantState.cardStatus = 'committed'
    renderPage()
    expect(screen.getByTestId('confirmation-card')).toBeInTheDocument()
    expect(screen.getByTestId('card-edit')).toBeInTheDocument()
    expect(screen.getByTestId('card-undo')).toBeInTheDocument()
    expect(screen.queryByTestId('history-card')).toBeNull()
  })

  it('新记一笔后上一张卡不消失（转为历史卡常驻）', () => {
    useAssistantChatStore.setState({ messages: [cardMsg('msg-1', 'prop-1', 'committed')] })
    mockAssistantState.activeProposal = proposal('prop-1')
    mockAssistantState.cardStatus = 'committed'
    renderPage()
    expect(screen.queryByTestId('history-card')).toBeNull()

    mockAssistantState.activeProposal = proposal('prop-2')
    mockAssistantState.cardStatus = 'executing'
    act(() => {
      useAssistantChatStore.setState({
        messages: [
          cardMsg('msg-1', 'prop-1', 'committed'),
          cardMsg('msg-2', 'prop-2', 'executing'),
        ],
      })
    })
    expect(screen.getAllByTestId('history-card')).toHaveLength(1)
    expect(within(screen.getByTestId('history-card')).getByTestId('card-amount-display')
      .textContent).toContain('45.00')
    expect(screen.getByTestId('confirmation-card')).toBeInTheDocument()
  })

  it('undone 历史卡：徽标「已撤销」+ 整卡灰化', () => {
    useAssistantChatStore.setState({
      messages: [cardMsg('msg-1', 'prop-1', 'undone', '已撤销')],
    })
    renderPage()
    expect(screen.getByTestId('card-status').textContent).toBe('已撤销')
    expect(screen.getByTestId('history-card').style.opacity).toBe('0.6')
  })

  it('error 历史卡：徽标红字「记账失败」，无「点编辑重试」引导', () => {
    useAssistantChatStore.setState({
      messages: [cardMsg('msg-1', 'prop-1', 'error', '❌ 记账失败：缺少分类或账本信息，请在卡片中补全后保存')],
    })
    renderPage()
    expect(screen.getByTestId('card-status').textContent).toContain('记账失败')
    expect(screen.getByText('记账失败，该笔未写入')).toBeInTheDocument()
    expect(screen.queryByText(/点「编辑」/)).toBeNull()
  })

  it('无 proposal 或 transactions 为空的消息不成卡', () => {
    useAssistantChatStore.setState({
      messages: [
        { id: 'msg-u', role: 'user', text: '午饭 45' },
        { id: 'msg-1', role: 'assistant', text: '查询到 3 笔记录', proposalStatus: 'committed' },
        {
          ...cardMsg('msg-2', 'prop-2', 'committed'),
          proposal: { ...proposal('prop-2'), transactions: [] },
        },
      ],
    })
    renderPage()
    expect(screen.queryByTestId('history-card')).toBeNull()
    expect(screen.getByText('查询到 3 笔记录')).toBeInTheDocument()
  })
})