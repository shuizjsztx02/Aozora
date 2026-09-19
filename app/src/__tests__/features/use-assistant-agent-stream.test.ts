/**
 * T-M7-02i：use-assistant 的 agent 流式消费测试。
 *
 * 覆盖卡面五条验收 + 02g-2 追加的三条渲染约束：
 * - 多步任务端到端可完成（流式 → done.executions → 回执 + 撤销窗）
 * - 多轮澄清可用（done.conversation_id 留存并带进下一轮）
 * - 非 chat intent 下流式气泡正确卸载并展开卡（done.proposals → pending-confirm）
 * - 开关关闭时回落既有单发链路（不调 runAgent）
 * - delta 按 channel 分流（reasoning 绝不混进正文）、多帧追加渲染、
 *   error 帧保留半截文本并标注失败
 * - 安全：挂起提案**未确认不写库**（T-M7-02e 决策 3 的确认门在客户端不被绕过）
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import {
  useAssistant, type AgentStreamEvent, type AssistantMessage,
} from '../../features/assistant/use-assistant'
import {
  useAssistantStream, type AgentStreamProjection,
} from '../../features/assistant/use-assistant-stream'
import { useAssistantChatStore } from '../../platform/store/assistant-chat-store'
import { parseInput } from '../../domain/nlu/parser'
import type { ActionProposal } from '../../domain/action-proposal'
import { todayBizDate } from '../../domain/date'

const mockBridge = vi.hoisted(() => ({
  insertTransaction: vi.fn().mockResolvedValue({ id: 'tx-1', version: 1 }),
  updateTransaction: vi.fn().mockResolvedValue({ id: 'tx-1', version: 2 }),
  listTransactionsByBook: vi.fn().mockResolvedValue([]),
  softDeleteTransaction: vi.fn().mockResolvedValue({ success: true }),
  triggerSync: vi.fn().mockResolvedValue(undefined),
  validateProposal: vi.fn().mockResolvedValue({ valid: true }),
  interpretAI: vi.fn(),
  runAgent: vi.fn(),
  cancelAgent: vi.fn().mockResolvedValue({ cancelled: true }),
  onAiStream: vi.fn(),
  insertBook: vi.fn().mockResolvedValue({ id: 'new-book' }),
  isAvailable: vi.fn().mockReturnValue(true),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

vi.mock('../../domain/nlu/parser', () => ({
  parseInput: vi.fn(),
}))

const TZ = 'Asia/Shanghai'
const STREAM_ID = 'stream-1'

/** 订阅者集合——`onAiStream` 的 mock 把 listener 收在这里，测试用 emit 推送事件。 */
let listeners: Array<(ev: AgentStreamEvent) => void> = []

function emit(event: string, data: Record<string, unknown>, streamId = STREAM_ID) {
  for (const l of [...listeners]) l({ streamId, event: event as AgentStreamEvent['event'], data })
}

/** 用**同一份订阅者快照**连推多个事件。
 *
 *  必须这样推：`done` / `error` 的收尾会同步退订，若每个事件各自去取当前的订阅者
 *  列表，第二个事件就送不到了——而「收尾之后又到一个事件」恰恰是要覆盖的场景
 *  （桥侧可能在流正常结束后补发一个本地 `error`，如连接关闭）。 */
function emitAll(events: Array<[string, Record<string, unknown>]>) {
  const snapshot = [...listeners]
  for (const [event, data] of events) {
    for (const l of snapshot) {
      l({ streamId: STREAM_ID, event: event as AgentStreamEvent['event'], data })
    }
  }
}

/** 一条必然走 AI 分支的本地提案（action=chat → needsAI 为真）。 */
function chatProposal(): ActionProposal {
  return {
    proposalId: 'prop-local',
    action: 'chat',
    confidence: 0.1,
    requiresConfirmation: false,
    message: '本地兜底',
    warnings: [],
    transactions: [],
  }
}

/** agent 路径下服务端挂起的记账提案（requiresConfirmation 恒为 True）。 */
function pendingProposal(): ActionProposal {
  return {
    proposalId: 'agent-abc123',
    action: 'add_tx',
    confidence: 0.95,
    requiresConfirmation: true,
    message: '记一笔午饭',
    warnings: [],
    transactions: [{
      type: 'expense',
      amountCents: 1200,
      categoryId: 'cat-food',
      bizDate: todayBizDate(TZ),
      note: '午饭',
    }],
  }
}

function setup(opts: { agentStream?: boolean; bookId?: string | null } = {}) {
  const { agentStream = true, bookId = 'book-1' } = opts
  return renderHook(() => useAssistant({
    categories: [], bookId, defaultAccountId: null, timezone: TZ, agentStream,
  }))
}

/** `done` 帧载荷的最小完整形状（字段表见 server/src/modules/ai/stream.py）。 */
function donePayload(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    conversation_id: 'conv-1', stop_reason: 'completed', steps: 1,
    tool_calls: 0, writes: 0, usage: { prompt_tokens: 1, completion_tokens: 1 },
    proposals: [], executions: [], pending_input: null,
    ...over,
  }
}

const lastMsg = (r: { current: { messages: AssistantMessage[] } }) =>
  r.current.messages[r.current.messages.length - 1]

describe('use-assistant — agent 流式消费（T-M7-02i）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    listeners = []
    mockBridge.onAiStream.mockImplementation((l: (ev: AgentStreamEvent) => void) => {
      listeners.push(l)
      return () => { listeners = listeners.filter(x => x !== l) }
    })
    mockBridge.runAgent.mockResolvedValue({ pending: true, streamId: STREAM_ID })
    mockBridge.isAvailable.mockReturnValue(true)
    mockBridge.interpretAI.mockResolvedValue(null)
    vi.mocked(parseInput).mockReturnValue(chatProposal())
    // store 是模块级单例：**连会话维度一起复位**——上一条用例 adopt 出的
    // activeConversationId 会带进下一轮的 runAgent 参数与消息桶
    useAssistantChatStore.getState().clear()
  })

  // ─── 开关与回落 ───

  it('开关关闭（默认）→ 回落既有 ai.interpret 链路，一次都不调 runAgent', async () => {
    const { result } = setup({ agentStream: false })

    await act(async () => { await result.current.submitInput('帮我记一笔午饭12块') })

    expect(mockBridge.runAgent).not.toHaveBeenCalled()
    expect(mockBridge.interpretAI).toHaveBeenCalledTimes(1)
    expect(result.current.streaming).toBeNull()
  })

  it('开关开启 → 走 agent 流式，不调 interpretAI', async () => {
    const { result } = setup({ agentStream: true })

    await act(async () => {
      const p = result.current.submitInput('帮我记一笔午饭12块')
      await Promise.resolve()
      emit('done', {
        conversation_id: 'conv-1', stop_reason: 'completed', steps: 2,
        tool_calls: 1, writes: 0, usage: { prompt_tokens: 1, completion_tokens: 1 },
        proposals: [], executions: [], pending_input: null,
      })
      await p
    })

    expect(mockBridge.runAgent).toHaveBeenCalledTimes(1)
    expect(mockBridge.interpretAI).not.toHaveBeenCalled()
  })

  // ─── 流式渲染（02g-2 的三条硬约束）───

  it('多帧 content delta 按追加渲染（不假设「一轮一帧」）', async () => {
    const { result } = setup()
    // `runAgent` 的 promise 由测试控制：这样才能把「推送一帧」各自放进独立的 act，
    // 让每次推送后的重渲染都 flush（全塞进一个 act 里读不到中间态）
    let release!: () => void
    mockBridge.runAgent.mockImplementationOnce(() => new Promise<void>(r => { release = r }))

    let submitting!: Promise<void>
    await act(async () => {
      submitting = result.current.submitInput('你好')
      await Promise.resolve()
    })

    const seen: string[] = []
    for (const chunk of ['你', '好', '呀']) {
      await act(async () => { emit('delta', { text: chunk, channel: 'content', step: 1 }) })
      seen.push(result.current.streaming?.text ?? '')
    }

    await act(async () => {
      emit('done', {
        conversation_id: 'conv-1', stop_reason: 'completed', steps: 1,
        tool_calls: 0, writes: 0, usage: { prompt_tokens: 1, completion_tokens: 1 },
        proposals: [], executions: [], pending_input: null,
      })
      release()
    })
    await act(async () => { await submitting })

    expect(seen).toEqual(['你', '你好', '你好呀'])
  })

  it('reasoning 与 content 分通道：推理绝不混进正文', async () => {
    const { result } = setup()
    let release!: () => void
    mockBridge.runAgent.mockImplementationOnce(() => new Promise<void>(r => { release = r }))

    let submitting!: Promise<void>
    await act(async () => {
      submitting = result.current.submitInput('你好')
      await Promise.resolve()
    })

    await act(async () => {
      emit('delta', { text: '先想想', channel: 'reasoning', step: 1 })
      emit('delta', { text: '你好呀', channel: 'content', step: 1 })
    })
    const bubble = result.current.streaming

    await act(async () => {
      emit('done', {
        conversation_id: 'conv-1', stop_reason: 'completed', steps: 1,
        tool_calls: 0, writes: 0, usage: { prompt_tokens: 1, completion_tokens: 1 },
        proposals: [], executions: [], pending_input: null,
      })
      release()
    })
    await act(async () => { await submitting })

    expect(bubble).toEqual({ text: '你好呀', reasoning: '先想想' })
  })

  it('流式期间 streaming 非空，done 后卸载（气泡让位给卡/消息）', async () => {
    const { result } = setup()
    let release!: () => void
    mockBridge.runAgent.mockImplementationOnce(() => new Promise<void>(r => { release = r }))

    let submitting!: Promise<void>
    await act(async () => {
      submitting = result.current.submitInput('你好')
      await Promise.resolve()
    })

    await act(async () => { emit('delta', { text: '你好', channel: 'content', step: 1 }) })
    const during = result.current.streaming

    await act(async () => {
      emit('done', {
        conversation_id: 'conv-1', stop_reason: 'completed', steps: 1,
        tool_calls: 0, writes: 0, usage: { prompt_tokens: 1, completion_tokens: 1 },
        proposals: [], executions: [], pending_input: null,
      })
      release()
    })
    await act(async () => { await submitting })

    expect(during).toEqual({ text: '你好', reasoning: '' })
    expect(result.current.streaming).toBeNull()
  })

  // ─── done 的三类落点 ───

  it('done.executions 非空 → 回执消息 + 事务 id 记入撤销窗 + triggerSync', async () => {
    const { result } = setup()

    await act(async () => {
      const p = result.current.submitInput('记一笔午饭12块')
      await Promise.resolve()
      emit('delta', { text: '记好了', channel: 'content', step: 2 })
      emit('done', {
        conversation_id: 'conv-1', stop_reason: 'completed', steps: 2,
        tool_calls: 1, writes: 1, usage: { prompt_tokens: 1, completion_tokens: 1 },
        proposals: [],
        executions: [{ proposal_id: 'agent-abc', transaction_ids: ['tx-9'], undo_window_seconds: 5 }],
        pending_input: null,
      })
      await p
    })

    const last = result.current.messages[result.current.messages.length - 1]
    expect(last.role).toBe('assistant')
    expect(last.text).toBe('记好了\n\n已记录 1 笔交易')
    expect(last.transactionIds).toEqual(['tx-9'])
    expect(mockBridge.triggerSync).toHaveBeenCalled()

    // 撤销窗可用：undoExecution 能删掉那一笔
    await act(async () => { await result.current.undoExecution() })
    expect(mockBridge.softDeleteTransaction).toHaveBeenCalledWith('tx-9', expect.any(String))
  })

  it('非 chat 产物（done.proposals）→ 流式气泡卸载并展开待确认卡', async () => {
    const { result } = setup()
    const proposal = pendingProposal()

    await act(async () => {
      const p = result.current.submitInput('先看看这个月花了多少，然后记一笔午饭12块')
      await Promise.resolve()
      emit('delta', { text: '先查了一下', channel: 'content', step: 3 })
      emit('done', {
        conversation_id: 'conv-1', stop_reason: 'completed', steps: 3,
        tool_calls: 3, writes: 1, usage: { prompt_tokens: 1, completion_tokens: 1 },
        proposals: [proposal as unknown as Record<string, unknown>],
        executions: [], pending_input: null,
      })
      await p
    })

    // 气泡卸载、卡展开
    expect(result.current.streaming).toBeNull()
    expect(result.current.activeProposal?.proposalId).toBe('agent-abc123')
    expect(result.current.cardStatus).toBe('pending-confirm')
    expect(result.current.phase).toBe('idle')

    const last = result.current.messages[result.current.messages.length - 1]
    expect(last.proposal?.proposalId).toBe('agent-abc123')
    expect(last.text).toBe('先查了一下')

    // **安全**：挂起的提案在用户确认之前一次库都没写（T-M7-02e 决策 3 的确认门）
    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
  })

  it('纯文本产物 → 气泡定型为正式消息（chat / query 落点）', async () => {
    const { result } = setup()

    await act(async () => {
      const p = result.current.submitInput('你好呀')
      await Promise.resolve()
      emit('delta', { text: '你好', channel: 'content', step: 1 })
      emit('done', {
        conversation_id: 'conv-1', stop_reason: 'completed', steps: 1,
        tool_calls: 0, writes: 0, usage: { prompt_tokens: 1, completion_tokens: 1 },
        proposals: [], executions: [], pending_input: null,
      })
      await p
    })

    const last = result.current.messages[result.current.messages.length - 1]
    expect(last.role).toBe('assistant')
    expect(last.text).toBe('你好')
    expect(result.current.activeProposal).toBeNull()
  })

  // ─── 失败路径 ───

  it('error 帧 → 保留半截文本并标注失败，不清空（架构 §8.5）', async () => {
    const { result } = setup()

    await act(async () => {
      const p = result.current.submitInput('你好')
      await Promise.resolve()
      emit('delta', { text: '我正在', channel: 'content', step: 1 })
      emit('error', { code: 'agent_failed', message: '本轮失败' })
      await p
    })

    expect(result.current.streaming).toBeNull()
    expect(result.current.phase).toBe('idle')
    const last = result.current.messages[result.current.messages.length - 1]
    expect(last.text).toBe('我正在\n\n⚠️ 本轮未完成，请重试')
  })

  it('桥层拒绝（旧 APK 无该命令）→ 同样按失败收尾，不静默回落', async () => {
    mockBridge.runAgent.mockRejectedValue(new Error('unknown command'))
    const { result } = setup()

    await act(async () => { await result.current.submitInput('你好') })

    expect(result.current.phase).toBe('idle')
    const last = result.current.messages[result.current.messages.length - 1]
    expect(last.text).toBe('⚠️ 本轮未完成，请重试')
    // 不静默回落单发：用户看得见失败，而不是被悄悄换一条链路
    expect(mockBridge.interpretAI).not.toHaveBeenCalled()
  })

  it('done 先到、error 后到（或反之）只收尾一次', async () => {
    const { result } = setup()

    await act(async () => {
      const p = result.current.submitInput('你好')
      await Promise.resolve()
      emitAll([
        ['done', {
          conversation_id: 'conv-1', stop_reason: 'completed', steps: 1,
          tool_calls: 0, writes: 0, usage: { prompt_tokens: 1, completion_tokens: 1 },
          proposals: [], executions: [], pending_input: null,
        }],
        ['error', { code: 'stream_ended', message: '连接关闭' }],
      ])
      await p
    })

    // 只有一条助手消息（done 的那条），迟到的 error 不再追加失败文案
    const assistants = result.current.messages.filter(m => m.role === 'assistant')
    expect(assistants).toHaveLength(1)
    expect(result.current.streaming).toBeNull()
  })

  // ─── 多轮 ───

  it('done.conversation_id 留存并在下一轮带上（否则每轮都开新会话）', async () => {
    const { result } = setup()

    await act(async () => {
      const p = result.current.submitInput('先看看这个月花了多少')
      await Promise.resolve()
      emit('done', {
        conversation_id: 'conv-42', stop_reason: 'completed', steps: 1,
        tool_calls: 0, writes: 0, usage: { prompt_tokens: 1, completion_tokens: 1 },
        proposals: [], executions: [], pending_input: null,
      })
      await p
    })

    expect(mockBridge.runAgent.mock.calls[0][2]).toBeNull()

    await act(async () => {
      const p = result.current.submitInput('那上个月呢')
      await Promise.resolve()
      emit('done', {
        conversation_id: 'conv-42', stop_reason: 'completed', steps: 1,
        tool_calls: 0, writes: 0, usage: { prompt_tokens: 1, completion_tokens: 1 },
        proposals: [], executions: [], pending_input: null,
      })
      await p
    })

    expect(mockBridge.runAgent.mock.calls[1][2]).toBe('conv-42')
  })

  // ─── 确认门（安全）───

  it('待确认卡被用户确认后才落库（确认门不在客户端被绕过）', async () => {
    const { result } = setup()
    const proposal = pendingProposal()

    await act(async () => {
      const p = result.current.submitInput('先看看这个月花了多少，然后记一笔午饭12块')
      await Promise.resolve()
      emit('done', {
        conversation_id: 'conv-1', stop_reason: 'completed', steps: 2,
        tool_calls: 2, writes: 1, usage: { prompt_tokens: 1, completion_tokens: 1 },
        proposals: [proposal as unknown as Record<string, unknown>],
        executions: [], pending_input: null,
      })
      await p
    })

    expect(result.current.cardStatus).toBe('pending-confirm')
    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()

    // 用户点卡内「确认」→ editProposal → 首次写入
    await act(async () => {
      await result.current.editProposal(proposal)
    })

    await waitFor(() => expect(result.current.cardStatus).toBe('committed'))
    expect(mockBridge.insertTransaction).toHaveBeenCalledTimes(1)
    const [tx] = mockBridge.insertTransaction.mock.calls[0]
    expect(tx.amountCents).toBe(1200)
    expect(tx.categoryId).toBe('cat-food')
    expect(result.current.messages[result.current.messages.length - 1].text).toBe('已记录 1 笔交易')
  })
})

describe('use-assistant — 流式消费重构（T-M7-03b）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    listeners = []
    mockBridge.onAiStream.mockImplementation((l: (ev: AgentStreamEvent) => void) => {
      listeners.push(l)
      return () => { listeners = listeners.filter(x => x !== l) }
    })
    mockBridge.runAgent.mockResolvedValue({ pending: true, streamId: STREAM_ID })
    mockBridge.cancelAgent.mockResolvedValue({ cancelled: true })
    mockBridge.isAvailable.mockReturnValue(true)
    mockBridge.interpretAI.mockResolvedValue(null)
    vi.mocked(parseInput).mockReturnValue(chatProposal())
    useAssistantChatStore.getState().clear()
  })

  // ─── 投影（八类事件里 P1 不渲染的那几类）───

  it('步骤帧进投影：streamProjection 拿到 phase/tool_call/tool_result，正文仍是空的', async () => {
    const { result } = setup()
    let release!: () => void
    mockBridge.runAgent.mockImplementationOnce(() => new Promise<void>(r => { release = r }))

    let submitting!: Promise<void>
    await act(async () => {
      submitting = result.current.submitInput('把上个月的餐饮都改成交通')
      await Promise.resolve()
    })

    await act(async () => {
      emit('phase', { phase: 'thinking', step: 1, reasoning: 'deep' })
      emit('tool_call', { tool: 'query_transactions', step: 1, message: '正在查询记录…' })
    })
    const running = result.current.streamProjection
    // 0 帧 delta：气泡里还没有正文，但「有反应」由步骤承担
    const runningBubble = result.current.streaming

    await act(async () => { emit('tool_result', { tool: 'query_transactions', step: 1, status: 'ok' }) })
    const finished = result.current.streamProjection

    await act(async () => {
      emit('done', donePayload({ steps: 1, tool_calls: 1 }))
      release()
    })
    await act(async () => { await submitting })

    expect(running?.steps).toEqual([{
      step: 1, phase: 'thinking', reasoning: 'deep',
      tool: 'query_transactions', message: '正在查询记录…', status: null, code: null,
    }])
    expect(running?.text).toBe('')
    expect(runningBubble).toEqual({ text: '', reasoning: '' })
    expect(finished?.steps[0].status).toBe('ok')
    // 收尾后投影卸载（气泡让位给正式消息）
    expect(result.current.streamProjection).toBeNull()
  })

  it('**不假设每条回复都从 delta 开始**：整轮 0 帧 delta 照常落回执', async () => {
    const { result } = setup()

    await act(async () => {
      const p = result.current.submitInput('记一笔午饭12块')
      await Promise.resolve()
      emit('phase', { phase: 'tool_calling', step: 1 })
      emit('tool_call', { tool: 'insert_transaction', step: 1, message: '正在记账…' })
      emit('tool_result', { tool: 'insert_transaction', step: 1, status: 'ok' })
      emit('done', donePayload({
        tool_calls: 1, writes: 1,
        executions: [{ proposal_id: 'agent-abc', transaction_ids: ['tx-7'], undo_window_seconds: 5 }],
      }))
      await p
    })

    // 没有半截文本 → 回执就是干净的一句，不带前导空行
    expect(lastMsg(result).text).toBe('已记录 1 笔交易')
    expect(lastMsg(result).transactionIds).toEqual(['tx-7'])
    expect(result.current.phase).toBe('idle')
  })

  // ─── 会话 id 提升到 store ───

  it('done 交回的 conversation_id 落进 store（不再只活在内存 ref）', async () => {
    const { result } = setup()

    await act(async () => {
      const p = result.current.submitInput('你好')
      await Promise.resolve()
      emit('done', donePayload({ conversation_id: 'conv-77' }))
      await p
    })

    const store = useAssistantChatStore.getState()
    expect(store.activeConversationId).toBe('conv-77')
    // 消息随之归到该会话名下：本地桶已改名，不留副本
    expect(store.projections['__local__']).toBeUndefined()
    expect(store.messages.some(m => m.text === '你好')).toBe(true)
  })

  it('会话切换互不串扰：两个会话各归各的桶，切回来原样', async () => {
    const { result } = setup()

    // 会话 A（首轮不带 id → 服务端交回 conv-A）
    await act(async () => {
      const p = result.current.submitInput('会话A的消息')
      await Promise.resolve()
      emit('done', donePayload({ conversation_id: 'conv-A', stop_reason: 'completed' }))
      await p
    })
    const aTexts = result.current.messages.map(m => m.text)
    expect(useAssistantChatStore.getState().activeConversationId).toBe('conv-A')

    // 切到新会话（03d 的「新建」：切到本地桶）
    act(() => { useAssistantChatStore.getState().switchConversation(null) })
    expect(result.current.messages).toEqual([])

    await act(async () => {
      const p = result.current.submitInput('会话B的消息')
      await Promise.resolve()
      emit('done', donePayload({ conversation_id: 'conv-B' }))
      await p
    })
    // 新会话首轮不带 conversation_id（不会串到 A 上）
    expect(mockBridge.runAgent.mock.calls[1][2]).toBeNull()
    expect(result.current.messages.map(m => m.text)).toEqual(['会话B的消息', '本轮没能得出结论，换个说法再试试？'])

    // 切回 A：原样，且不带 B 的任何一条
    act(() => { useAssistantChatStore.getState().switchConversation('conv-A') })
    expect(result.current.messages.map(m => m.text)).toEqual(aTexts)
    expect(result.current.messages.some(m => m.text === '会话B的消息')).toBe(false)

    // 再切到 B：同样原样
    act(() => { useAssistantChatStore.getState().switchConversation('conv-B') })
    expect(result.current.messages.map(m => m.text))
      .toEqual(['会话B的消息', '本轮没能得出结论，换个说法再试试？'])
  })

  it('**流式期间**切走会话：迟到的 done 不把用户拽回旧会话（03d 的列表会允许这么做）', async () => {
    const { result } = setup()
    let release!: () => void
    mockBridge.runAgent.mockImplementationOnce(() => new Promise<void>(r => { release = r }))

    let submitting!: Promise<void>
    await act(async () => {
      submitting = result.current.submitInput('会话A的消息')
      await Promise.resolve()
    })

    // 流式还没结束就切到另一个会话
    act(() => { useAssistantChatStore.getState().switchConversation('conv-B') })
    expect(result.current.messages).toEqual([])

    await act(async () => {
      emit('done', donePayload({ conversation_id: 'conv-A' }))
      release()
    })
    await act(async () => { await submitting })

    // 本轮交回的 id 属于「已被切走」的会话——不采纳，用户仍在 conv-B
    expect(useAssistantChatStore.getState().activeConversationId).toBe('conv-B')
  })

  // ─── 取消（ai.cancel 接线）───

  it('取消（有活动流）→ 气泡卸载、三态收敛为空、零写、半截文本保留', async () => {
    const { result } = setup()
    const proposal = pendingProposal()

    // 第一轮留下一个待确认卡（三态之一）
    await act(async () => {
      const p = result.current.submitInput('先看看这个月花了多少，然后记一笔午饭12块')
      await Promise.resolve()
      emit('done', donePayload({
        steps: 2, tool_calls: 2, writes: 1,
        proposals: [proposal as unknown as Record<string, unknown>],
      }))
      await p
    })
    expect(result.current.activeProposal).not.toBeNull()
    expect(result.current.cardStatus).toBe('pending-confirm')

    // 第二轮：流式中途取消
    mockBridge.runAgent.mockImplementationOnce(() => new Promise<void>(() => { /* 永不 resolve */ }))
    let submitting!: Promise<void>
    await act(async () => {
      submitting = result.current.submitInput('那上个月呢')
      await Promise.resolve()
    })
    await act(async () => { emit('delta', { text: '正想说', channel: 'content', step: 1 }) })
    expect(result.current.streaming).toEqual({ text: '正想说', reasoning: '' })

    await act(async () => { await result.current.cancelStream() })

    // ai.cancel 真发出去了；本地态随即收敛
    expect(mockBridge.cancelAgent).toHaveBeenCalledTimes(1)
    expect(result.current.streaming).toBeNull()
    expect(result.current.streamProjection).toBeNull()
    expect(result.current.activeProposal).toBeNull()
    expect(result.current.cardStatus).toBeNull()
    expect(result.current.pendingOp).toBeNull()
    expect(result.current.pendingConfig).toBeNull()
    expect(result.current.phase).toBe('idle')

    // 零写：取消只关连接，不落任何一笔
    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
    expect(mockBridge.updateTransaction).not.toHaveBeenCalled()
    expect(mockBridge.softDeleteTransaction).not.toHaveBeenCalled()

    // 半截文本保留并标注取消（与「本轮失败」是两种说法，03e 据此分流）
    expect(lastMsg(result).text).toBe('正想说\n\n已取消')

    // 无活动流时再取消一次：不重复收尾（不追加第二条消息）
    const count = result.current.messages.length
    await act(async () => { await result.current.cancelStream() })
    expect(result.current.messages).toHaveLength(count)
    void submitting
  })

  it('取消（无活动流）→ 未决的 pendingOp 一并作废且零写', async () => {
    // 未决卡由 AI 单发路径产生（agent 流的中间态不做 intent 判定）
    mockBridge.interpretAI.mockResolvedValue({
      output: {
        intent: 'edit', reply: '好的，帮你改',
        filter: { keyword: '打车' }, set: { amountCents: 500 },
      },
    })
    mockBridge.listTransactionsByBook.mockResolvedValue([{
      id: 'tx-1', bookId: 'book-1', type: 'expense', amountCents: 2300, categoryId: 'c-food',
      accountId: null, note: '打车', bizDate: '2026-09-08', occurredAt: 1,
      source: 'manual', version: 7, deletedAt: null,
    }])
    const { result } = setup({ agentStream: false })

    await act(async () => { await result.current.submitInput('把打车那笔改成5块') })
    expect(result.current.pendingOp).not.toBeNull()

    await act(async () => { await result.current.cancelStream() })

    expect(result.current.pendingOp).toBeNull()
    expect(mockBridge.updateTransaction).not.toHaveBeenCalled()
    expect(mockBridge.softDeleteTransaction).not.toHaveBeenCalled()
  })

  it('取消（无活动流）→ 未决的 pendingConfig 一并作废且零写', async () => {
    mockBridge.interpretAI.mockResolvedValue({
      output: {
        intent: 'config', reply: '好的',
        type: '', amountCents: 0, category: '', note: '', bizDate: '', confidence: 0.9,
        config: { op: 'book.create', name: '旅行' },
      },
    })
    const { result } = setup({ agentStream: false })

    await act(async () => { await result.current.submitInput('新建一个叫旅行的账本') })
    expect(result.current.pendingConfig).not.toBeNull()

    await act(async () => { await result.current.cancelStream() })

    expect(result.current.pendingConfig).toBeNull()
    expect(mockBridge.insertBook).not.toHaveBeenCalled()
  })
})

describe('useAssistantStream — 轮次归属（T-M7-03b 收口）', () => {
  it('上一轮迟到的桥层失败不收尾新一轮', async () => {
    // 直接在 hook 层构造「流未收尾就又起一轮」：useAssistant 的并发互斥挡住了这条路，
    // 但那是**另一个模块**的假设，本模块不该依赖它。
    let listeners: Array<(ev: AgentStreamEvent) => void> = []
    let rejectFirst!: (e: unknown) => void
    const bridge = {
      runAgent: vi.fn()
        .mockImplementationOnce(() => new Promise((_, rej) => { rejectFirst = rej }))
        .mockResolvedValue({ pending: true, streamId: 's-2' }),
      onAiStream: (l: (ev: AgentStreamEvent) => void) => {
        listeners.push(l)
        return () => { listeners = listeners.filter(x => x !== l) }
      },
    }
    const settled: AgentStreamProjection[] = []
    const { result } = renderHook(() => useAssistantStream({
      bridge, onSettled: p => settled.push(p),
    }))

    let first!: Promise<void>
    await act(async () => {
      first = result.current.start('第一轮', 'book-1', null)
      await Promise.resolve()
    })
    expect(result.current.projection).not.toBeNull()

    // 第二轮顶替第一轮（第一轮的 runAgent 还挂着）
    await act(async () => { await result.current.start('第二轮', 'book-1', null) })

    // 第一轮此时才失败——不该把第二轮收尾掉
    await act(async () => { rejectFirst(new Error('boom')); await first })

    expect(settled).toHaveLength(0)
    expect(result.current.projection).not.toBeNull()
    expect(result.current.projection?.settled).toBe(false)
  })

  it('取消只收尾当前轮，且只收尾一次', async () => {
    let listeners: Array<(ev: AgentStreamEvent) => void> = []
    const bridge = {
      runAgent: vi.fn().mockResolvedValue({ pending: true, streamId: 's-1' }),
      onAiStream: (l: (ev: AgentStreamEvent) => void) => {
        listeners.push(l)
        return () => { listeners = listeners.filter(x => x !== l) }
      },
      cancelAgent: vi.fn().mockResolvedValue({ cancelled: true }),
    }
    const settled: AgentStreamProjection[] = []
    const { result } = renderHook(() => useAssistantStream({
      bridge, onSettled: p => settled.push(p),
    }))

    await act(async () => { await result.current.start('你好', 'book-1', null) })
    await act(async () => {
      for (const l of [...listeners]) l({ streamId: 's-1', event: 'delta', data: { text: '半句', channel: 'content', step: 1 } })
    })

    await act(async () => { await result.current.cancel() })
    expect(settled).toHaveLength(1)
    expect(settled[0]).toMatchObject({ canceled: true, settled: true, text: '半句' })
    expect(result.current.projection).toBeNull()

    // 再取消一次：没有活动流，不产生第二次收尾
    await act(async () => { await result.current.cancel() })
    expect(settled).toHaveLength(1)
  })
})
/**
 * T-M7-03d：**陈旧 `conversation_id` 的 404 降级**（03a 交办、03b 记录在案的缺口）。
 *
 * 服务端对「别人的 / 已删除的 / 不存在的 id」一律 404，且是在 `POST /ai/agent`
 * **开流之前**抛出——桥把它译成一帧 `error{code:'not_found'}`（03d 新增的映射，
 * 此前它与普通网络故障不可分辨）。终态是 03b 交接的那一条：**丢弃这个 id，
 * 下一轮开新会话**，而不是让用户对着一句「请重试」永远重试同一个死 id。
 */
describe('use-assistant — 陈旧 conversation_id 的 404 降级（T-M7-03d）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    listeners = []
    mockBridge.onAiStream.mockImplementation((l: (ev: AgentStreamEvent) => void) => {
      listeners.push(l)
      return () => { listeners = listeners.filter(x => x !== l) }
    })
    mockBridge.runAgent.mockResolvedValue({ pending: true, streamId: STREAM_ID })
    mockBridge.isAvailable.mockReturnValue(true)
    mockBridge.interpretAI.mockResolvedValue(null)
    vi.mocked(parseInput).mockReturnValue(chatProposal())
    useAssistantChatStore.getState().clear()
  })

  it('not_found 帧：丢弃陈旧 id、这一屏消息原地保留、下一轮不带 id', async () => {
    // 上一轮留下的会话 id（服务端已经没有了：别的设备删了 / 换了账号登录）
    useAssistantChatStore.setState({
      activeConversationId: 'c-stale',
      messages: [{ id: 'u0', role: 'user', text: '上一条消息' }],
    })
    const { result } = setup()

    await act(async () => {
      const p = result.current.submitInput('帮我记一笔午饭12块')
      await Promise.resolve()
      emitAll([
        ['delta', { text: '我先看看', channel: 'content', step: 1 }],
        ['error', { code: 'not_found', message: 'Conversation not found' }],
      ])
      await p
    })

    // ① id 丢掉 —— 下一轮不带它，服务端因此开新会话
    expect(useAssistantChatStore.getState().activeConversationId).toBeNull()
    // ② 半截文本保留 + 一句明确的失效说明（不是泛泛的「请重试」）
    const last = lastMsg(result).text
    expect(last).toContain('我先看看')
    expect(last).toContain('已切回本地会话')
    // ③ 之前那一屏的消息一条不丢
    expect(result.current.messages.some(m => m.text === '上一条消息')).toBe(true)

    // ④ 下一轮确实没带 id
    await act(async () => { await result.current.submitInput('再来一次') })
    expect(mockBridge.runAgent).toHaveBeenLastCalledWith('再来一次', 'book-1', null)
  })

  it('普通失败（network）不走这条降级：id 留着，用户重试同一会话', async () => {
    useAssistantChatStore.setState({ activeConversationId: 'c-ok', messages: [] })
    const { result } = setup()

    await act(async () => {
      const p = result.current.submitInput('帮我记一笔午饭12块')
      await Promise.resolve()
      emitAll([['error', { code: 'network', message: 'Network error' }]])
      await p
    })

    expect(useAssistantChatStore.getState().activeConversationId).toBe('c-ok')
    expect(lastMsg(result).text).toContain('本轮未完成')
  })
})
