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
import { useAssistant, type AgentStreamEvent } from '../../features/assistant/use-assistant'
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
    useAssistantChatStore.setState({ messages: [] })
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