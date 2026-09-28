/**
 * T-M7-02i：聊天页的 agent 流式气泡渲染。
 *
 * 验证（架构 §8.5 的客户端契约）：
 * - 流式期间渲染气泡（正文按追加文本渲染，不假设「一轮一帧」）
 * - 推理走**灰字折叠区**（<details>），且推理文本**不出现在正文里**（两个通道不混）
 * - 流式气泡在场时不再叠一个「思考中…」（两者是同一件事的两种说法）
 * - done/error 之后 streaming 置空 → 气泡卸载，让位给卡与正式消息
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { AssistantPage } from '../../features/assistant/AssistantPage'
import { useAssistantChatStore } from '../../platform/store/assistant-chat-store'
import type { AssistantMessage } from '../../features/assistant/use-assistant'

const hookState = vi.hoisted(() => ({
  phase: 'idle' as 'idle' | 'processing' | 'executing',
  streaming: null as { text: string; reasoning: string } | null,
  interrupted: null as { input: string; note?: string } | null,
  cancelStream: vi.fn(),
  retryInterrupted: vi.fn(),
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
}))

vi.mock('../../platform/hooks/use-bridge', () => ({ useBridge: () => mockBridge }))
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
      submitInput: vi.fn(),
      editProposal: vi.fn(),
      undoExecution: vi.fn(),
      confirmOp: vi.fn(),
      cancelOp: vi.fn(),
      confirmConfig: vi.fn(),
      cancelConfig: vi.fn(),
      cancelStream: hookState.cancelStream,
      rejectApproval: vi.fn(),
      interrupted: hookState.interrupted,
      retryInterrupted: hookState.retryInterrupted,
      appendAssistantMessage: vi.fn(),
    }),
  }
})

const userMsg = (id: string, text: string): AssistantMessage => ({ id, role: 'user', text })

const renderPage = () =>
  render(
    <MemoryRouter>
      <AssistantPage />
    </MemoryRouter>,
  )

describe('AssistantPage — agent 流式气泡（T-M7-02i）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
    hookState.phase = 'idle'
    hookState.streaming = null
    hookState.interrupted = null
    useAssistantChatStore.setState({ messages: [] })
  })

  it('流式期间渲染气泡，正文按追加文本呈现', async () => {
    hookState.phase = 'processing'
    hookState.streaming = { text: '你好呀', reasoning: '' }
    useAssistantChatStore.setState({ messages: [userMsg('u1', '你好')] })
    renderPage()

    const bubble = await screen.findByTestId('streaming-bubble')
    expect(bubble).toHaveTextContent('你好呀')
    // 气泡在场时不再叠「思考中…」
    expect(screen.queryByText('思考中…')).toBeNull()
  })

  it('推理进灰字折叠区，且不混进正文', async () => {
    hookState.phase = 'processing'
    hookState.streaming = { text: '这个月共支出 500 元', reasoning: '先查一下这个月的流水' }
    renderPage()

    const fold = await screen.findByTestId('streaming-reasoning')
    expect(fold).toHaveTextContent('深度思考')
    // 推理文本在折叠区里（<details> 内），正文气泡里没有它
    const bubble = screen.getByTestId('streaming-bubble')
    expect(bubble).toHaveTextContent('先查一下这个月的流水')
    expect(bubble.textContent).toContain('这个月共支出 500 元')
    // 正文与推理是两个节点，不是拼在一起的一句话
    const details = bubble.querySelector('details')
    expect(details?.textContent).toContain('先查一下这个月的流水')
    expect(details?.textContent).not.toContain('这个月共支出 500 元')
  })

  it('暂无正文时气泡显示「思考中…」占位', async () => {
    hookState.phase = 'processing'
    hookState.streaming = { text: '', reasoning: '正在理清需求' }
    renderPage()

    const bubble = await screen.findByTestId('streaming-bubble')
    expect(bubble).toHaveTextContent('思考中…')
  })

  it('streaming 置空（done / error 之后）→ 气泡卸载，思考中指示回归常规路径', async () => {
    hookState.phase = 'processing'
    hookState.streaming = { text: '半截', reasoning: '' }
    const { rerender } = renderPage()
    expect(await screen.findByTestId('streaming-bubble')).toBeInTheDocument()

    hookState.streaming = null
    rerender(
      <MemoryRouter>
        <AssistantPage />
      </MemoryRouter>,
    )
    expect(screen.queryByTestId('streaming-bubble')).toBeNull()
    expect(screen.getByText('思考中…')).toBeInTheDocument()
  })

  // ─── T-M7-03e：取消入口与中断提示 ───

  it('流式期间发送位换成「停止」→ 触发 ai.cancel（审批四值的 cancelled）', async () => {
    hookState.phase = 'processing'
    hookState.streaming = { text: '正想说', reasoning: '' }
    const { rerender } = renderPage()

    const stop = await screen.findByTestId('stop-generating')
    expect(screen.queryByText('发送')).toBeNull()
    fireEvent.click(stop)
    expect(hookState.cancelStream).toHaveBeenCalledTimes(1)

    // 非流式（快路径）时回到「发送」——快路径的 12 s / 25 s 体验不变
    hookState.streaming = null
    hookState.phase = 'idle'
    rerender(
      <MemoryRouter>
        <AssistantPage />
      </MemoryRouter>,
    )
    expect(await screen.findByText('发送')).toBeInTheDocument()
    expect(screen.queryByTestId('stop-generating')).toBeNull()
  })

  it('中断提示条 + 重连入口（R-PROD-19：断连是被动的，与「已取消」分开）', async () => {
    hookState.interrupted = { input: '先看看这个月花了多少' }
    renderPage()

    const banner = await screen.findByTestId('interrupted-banner')
    expect(banner).toHaveTextContent('连接中断')
    fireEvent.click(screen.getByTestId('interrupted-retry'))
    expect(hookState.retryInterrupted).toHaveBeenCalledTimes(1)
  })

  it('★ T20-9：服务端明确拒绝（配额用尽）→ 提示条用如实文案，不落「连接中断」', async () => {
    // REQ-82：气泡与提示条是两处文案，两处都不能说「连接中断」——只改气泡的话
    // 用户在同一屏上仍会看到那句谎（提示条就在输入框上方）。
    hookState.interrupted = { input: '再记一笔', note: '⚠️ 今天的 AI 额度用完了，明天再试试' }
    renderPage()

    const banner = await screen.findByTestId('interrupted-banner')
    expect(banner).toHaveTextContent('额度用完了')
    expect(banner).not.toHaveTextContent('连接中断')
    // 重发入口照旧在（限流一会儿就恢复；配额到顶时重发也无害）
    expect(screen.getByTestId('interrupted-retry')).toBeInTheDocument()
  })
})