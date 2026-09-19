/**
 * T-M7-03c：步骤卡片流与统一智能模式。
 *
 * 卡面验收落点：
 * - 三态渲染齐全（进行中 / 完成 / 失败，含 `tool_result` 失败态）
 * - **简单任务无步骤区**（统一智能模式，架构 §9.1）
 * - 折叠 / 展开
 * - 多帧 `delta` 追加渲染 → 页面侧对应的是「**不假设每条回复都从 delta 开始**」：
 *   只有步骤帧的一轮（快档直调工具，0 帧 delta）步骤区在场、正文走占位
 * - 空 / 加载 / 失败 / 离线四态回归
 *
 * 投影怎么算（八类事件 → 投影态）由 03b 的 `assistant-stream-projection.test.ts`
 * 钉住；本文件只管**渲染**，故直接给出投影。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { AssistantPage } from '../../features/assistant/AssistantPage'
import {
  createStreamProjection, type AgentStepView, type AgentStreamProjection,
} from '../../features/assistant/use-assistant-stream'
import { useAssistantChatStore } from '../../platform/store/assistant-chat-store'
import type { AssistantMessage } from '../../features/assistant/use-assistant'

const hookState = vi.hoisted(() => ({
  phase: 'idle' as 'idle' | 'processing' | 'executing',
  streaming: null as { text: string; reasoning: string } | null,
  streamProjection: null as unknown,
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
      streamProjection: hookState.streamProjection,
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

/** 一个 Step 的投影（默认进行中）。 */
function step(over: Partial<AgentStepView> = {}): AgentStepView {
  return {
    step: 1, phase: 'tool_calling', reasoning: null, tool: null,
    message: null, status: null, code: null, ...over,
  }
}

/** 只带步骤的投影（正文与推理都空——「0 帧 delta 的一轮」的正常形状）。 */
function projectionWith(steps: AgentStepView[], over: Partial<AgentStreamProjection> = {}) {
  return { ...createStreamProjection(), steps, ...over }
}

/** 让页面进入「流式中」并挂上给定投影。 */
function streaming(proj: AgentStreamProjection, text = '', reasoning = '') {
  hookState.phase = 'processing'
  hookState.streamProjection = proj
  hookState.streaming = { text, reasoning }
}

const userMsg = (id: string, text: string): AssistantMessage => ({ id, role: 'user', text })

const renderPage = () =>
  render(
    <MemoryRouter>
      <AssistantPage />
    </MemoryRouter>,
  )

describe('AssistantPage — 步骤卡片流（T-M7-03c）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
    hookState.phase = 'idle'
    hookState.streaming = null
    hookState.streamProjection = null
    useAssistantChatStore.setState({ messages: [], activeConversationId: null, projections: {} })
  })

  it('三态齐全：进行中 / 完成 / 失败各成一行', async () => {
    streaming(projectionWith([
      step({ step: 1, tool: 'budget_list', message: '正在查询预算…' }),
      step({ step: 2, tool: 'budget_status', message: '正在核算预算执行情况…', status: 'ok' }),
      step({
        step: 3, tool: 'transaction_add', message: '正在记账…',
        status: 'error', code: 'unknown_book',
      }),
    ]))
    renderPage()

    expect(await screen.findByTestId('step-stream')).toBeInTheDocument()
    expect(screen.getByTestId('step-row-1')).toHaveAttribute('data-status', 'running')
    expect(screen.getByTestId('step-row-2')).toHaveAttribute('data-status', 'ok')
    expect(screen.getByTestId('step-row-3')).toHaveAttribute('data-status', 'error')

    // 进行中：服务端进度句原样；完成：换成「已…」；失败：安全码 → 用户向文案
    expect(screen.getByTestId('step-row-1')).toHaveTextContent('正在查询预算…')
    expect(screen.getByTestId('step-row-2')).toHaveTextContent('已核算预算执行情况…')
    expect(screen.getByTestId('step-row-3')).toHaveTextContent('账本不存在')
    // 失败文案**不是**服务端回喂模型的那句指令
    expect(screen.getByTestId('step-row-3')).not.toHaveTextContent('请先查询账本清单')
  })

  it('失败的未知码走兜底文案，不透传任何服务端原文', async () => {
    streaming(projectionWith([
      step({ step: 1, tool: 'x', message: '正在处理…', status: 'error', code: 'weird_new_code' }),
    ]))
    renderPage()

    expect(await screen.findByTestId('step-row-1')).toHaveTextContent('这一步没有完成')
    expect(screen.getByTestId('step-row-1')).not.toHaveTextContent('weird_new_code')
  })

  it('简单任务（本回合无 tool_call）不展示步骤区', async () => {
    // 纯文本答复的一轮：只有 delta，steps 为空
    streaming(projectionWith([]), '这个月共支出 500 元')
    renderPage()

    expect(await screen.findByTestId('streaming-bubble')).toHaveTextContent('这个月共支出 500 元')
    expect(screen.queryByTestId('step-stream')).toBeNull()
  })

  it('只有 phase:thinking 的步不单独成行（没有人类可读载荷）', async () => {
    streaming(projectionWith([step({ step: 1, phase: 'thinking' })]), '', '先想想怎么算')
    renderPage()

    expect(await screen.findByTestId('streaming-bubble')).toBeInTheDocument()
    expect(screen.queryByTestId('step-stream')).toBeNull()
  })

  it('0 帧 delta 的一轮：步骤区在场，正文走占位而不是空白', async () => {
    streaming(projectionWith([
      step({ step: 1, tool: 'transaction_add', message: '正在记账…' }),
      step({ step: 1, tool: 'transaction_add', message: '正在记账…', status: 'ok' }),
    ]))
    renderPage()

    expect(await screen.findByTestId('step-stream')).toBeInTheDocument()
    expect(screen.getByTestId('streaming-bubble')).toHaveTextContent('思考中…')
  })

  it('折叠 / 展开：标题行是唯一的折叠粒度', async () => {
    streaming(projectionWith([
      step({ step: 1, tool: 'budget_list', message: '正在查询预算…' }),
      step({ step: 2, tool: 'budget_status', message: '正在核算预算执行情况…', status: 'ok' }),
    ]))
    renderPage()

    const toggle = await screen.findByTestId('step-stream-toggle')
    expect(toggle).toHaveTextContent('步骤 · 2 步')
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByTestId('step-stream-list')).toBeInTheDocument()

    await userEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByTestId('step-stream-list')).toBeNull()

    await userEvent.click(toggle)
    expect(screen.getByTestId('step-stream-list')).toBeInTheDocument()
  })

  it('routing 阶段的空态占位（生产者归 P4 的 05g）', async () => {
    streaming(projectionWith([step({ step: 1, phase: 'routing' })]))
    renderPage()

    expect(await screen.findByTestId('step-row-1')).toHaveTextContent('正在理解你的需求…')
  })

  it('deep 档位显示「深度思考」徽标，其它档位不显示', async () => {
    streaming(projectionWith([
      step({ step: 1, reasoning: 'deep', tool: 'budget_list', message: '正在查询预算…' }),
      step({ step: 2, reasoning: 'fast', tool: 'account_list', message: '正在查询账户…', status: 'ok' }),
    ]))
    renderPage()

    expect(await screen.findByTestId('step-deep-1')).toBeInTheDocument()
    expect(screen.queryByTestId('step-deep-2')).toBeNull()
  })

  it('推理折叠区与步骤区、正文三者在同一气泡内且互不混', async () => {
    streaming(
      projectionWith([step({ step: 1, tool: 'budget_list', message: '正在查询预算…' })]),
      '本月预算还剩 300 元',
      '先看看预算',
    )
    renderPage()

    const bubble = await screen.findByTestId('streaming-bubble')
    // 步骤区、折叠区、正文是三个节点，不是拼在一起的一句话
    expect(bubble.querySelector('[data-testid="step-stream"]')).not.toBeNull()
    const details = bubble.querySelector('details')
    expect(details?.textContent).toContain('先看看预算')
    expect(details?.textContent).not.toContain('本月预算还剩 300 元')
    expect(details?.textContent).not.toContain('正在查询预算…')
    // 折叠区标题给出「还在思考」的说法
    expect(screen.getByTestId('streaming-reasoning')).toHaveTextContent('深度思考')
  })
})

describe('AssistantPage — 空 / 加载 / 失败 / 离线四态回归（T-M7-03c）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
    hookState.phase = 'idle'
    hookState.streaming = null
    hookState.streamProjection = null
    useAssistantChatStore.setState({ messages: [], activeConversationId: null, projections: {} })
  })

  it('空态：没有消息也没有流 → 引导文案，且无步骤区', async () => {
    renderPage()

    expect(await screen.findByText('输入记账短句，如「午饭 12.5」')).toBeInTheDocument()
    expect(screen.queryByTestId('step-stream')).toBeNull()
  })

  it('加载态：处理中且尚无任何帧 → 「思考中…」，不出现空步骤区', async () => {
    hookState.phase = 'processing'
    renderPage()

    expect(await screen.findByText('思考中…')).toBeInTheDocument()
    expect(screen.queryByTestId('streaming-bubble')).toBeNull()
    expect(screen.queryByTestId('step-stream')).toBeNull()
  })

  it('失败态：半截文本保留在历史消息里（不清空），步骤区随气泡卸载', async () => {
    // error 帧到达后由 hook 定型：保留半截文本 + 标注本轮未完成（架构 §8.5）
    useAssistantChatStore.setState({
      messages: [
        userMsg('u1', '帮我看看这个月预算'),
        { id: 'a1', role: 'assistant', text: '这个月预算还剩\n\n⚠️ 本轮未完成，请重试' },
      ],
    })
    renderPage()

    expect(await screen.findByText(/这个月预算还剩/)).toBeInTheDocument()
    expect(screen.getByText(/本轮未完成/)).toBeInTheDocument()
    expect(screen.queryByTestId('step-stream')).toBeNull()
  })

  it('离线态：桥不可用（无流式）时页面照常可用，不报错、无步骤区', async () => {
    mockBridge.isAvailable.mockReturnValue(false)
    useAssistantChatStore.setState({ messages: [userMsg('u1', '午饭 12.5')] })
    renderPage()

    expect(await screen.findByText('午饭 12.5')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('输入记账短句…')).toBeEnabled()
    expect(screen.queryByTestId('step-stream')).toBeNull()
  })
})