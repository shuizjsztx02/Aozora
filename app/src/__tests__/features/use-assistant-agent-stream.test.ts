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
import { resetCloudReachabilityForTest } from '../../platform/cloud-reachability'
import { parseInput } from '../../domain/nlu/parser'
import type { ActionProposal, ActionProposalV2 } from '../../domain/action-proposal'
import { isV2Proposal } from '../../domain/action-proposal'
import { todayBizDate } from '../../domain/date'

const mockBridge = vi.hoisted(() => ({
  insertTransaction: vi.fn().mockResolvedValue({ id: 'tx-1', version: 1 }),
  updateTransaction: vi.fn().mockResolvedValue({ id: 'tx-1', version: 2 }),
  listTransactionsByBook: vi.fn().mockResolvedValue([]),
  softDeleteTransaction: vi.fn().mockResolvedValue({ success: true }),
  triggerSync: vi.fn().mockResolvedValue(undefined),
  // T18-2（REQ-67①）：写后走**阻塞式真同步**（auth.triggerSync），不再是入队式 triggerSync
  triggerAuthSync: vi.fn().mockResolvedValue({ outcome: 'OK' }),
  validateProposal: vi.fn().mockResolvedValue({ valid: true }),
  // T18-3（REQ-67②）：v2 提案确认后的执行入口（`/ai/execute`）——回执即执行凭据
  executeProposal: vi.fn(),
  interpretAI: vi.fn(),
  runAgent: vi.fn(),
  cancelAgent: vi.fn().mockResolvedValue({ cancelled: true }),
  onAiStream: vi.fn(),
  // T-M7-04b：断连续读（默认**不装**——与真实能力探测同形，见文件末尾那组用例）
  fetchConversationEvents: undefined as undefined | ReturnType<typeof vi.fn>,
  insertBook: vi.fn().mockResolvedValue({ id: 'new-book' }),
  isAvailable: vi.fn().mockReturnValue(true),
  // T18-8（REQ-71）：`auth.me` = 可达性探测的探针；resolve = 云端可达
  // （把 isAvailable 置 false 即模拟离线，走本地快路径）
  getMe: vi.fn().mockResolvedValue({}),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

vi.mock('../../domain/nlu/parser', () => ({
  parseInput: vi.fn(),
}))

// T18-8：可达性探测是模块级单例（带 30s 缓存）——逐用例复位，否则上一条用例的
// 「在线」判定会漏进下一条。
beforeEach(() => { resetCloudReachabilityForTest() })

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

/**
 * 「发出输入 → 等 stream 订阅好 → 再推帧」之间的等待。
 *
 * T18-8 起 `submitInput` 起手多了一次 `await`（可达性探测，见 `platform/cloud-reachability.ts`），
 * 探测链要走几个微任务才轮到 `runAgent`/`onAiStream`。原先一句 `await Promise.resolve()`
 * 就够（微任务一轮），现在不够——帧会推在订阅之前、**静默丢掉**（表现为「气泡不出现」
 * 或「消息流里只剩 user 那条」）。一个宏任务会把待处理的微任务全部排干，比数轮数稳。
 */
const flush = () => new Promise<void>(resolve => { setTimeout(resolve, 0) })

/** 一条本地解析产物（action=chat）——T18-8 之后走哪条路只看可达性，它只用来
 *  证明「在线时本地解析结果不影响落点」。 */
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

/** T18-3（REQ-67②）：服务端挂起的 **v2** 提案（`contractVersion` 在场即 v2 车道）。
 *  v2 的 payload 形状由服务端工具注册表拥有，客户端只原样携带。 */
function pendingV2Proposal(action = 'transaction_update'): ActionProposalV2 {
  return {
    contractVersion: 2,
    proposalId: 'agent-v2-abc123',
    action,
    confidence: 0.95,
    requiresConfirmation: true,
    message: '把「祖传牛肉面」的金额改成 ¥15.00',
    warnings: [],
    payload: { transactionId: 'tx-1', version: 3, changes: { amountCents: 1500 } },
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
      await flush()
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
      await flush()
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
      await flush()
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
      await flush()
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

  it('done.executions 非空 → 回执消息 + 事务 id 记入撤销窗 + 写后真同步', async () => {
    const { result } = setup()

    await act(async () => {
      const p = result.current.submitInput('记一笔午饭12块')
      await flush()
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
    // T18-2（REQ-67①）：服务端已写 → 客户端跑一轮**阻塞式真同步**把权威行拉回本地
    expect(mockBridge.triggerAuthSync).toHaveBeenCalled()
    expect(mockBridge.triggerSync).not.toHaveBeenCalled()

    // 撤销窗可用：undoExecution 能删掉那一笔
    await act(async () => { await result.current.undoExecution() })
    expect(mockBridge.softDeleteTransaction).toHaveBeenCalledWith('tx-9', expect.any(String))
  })

  it('非 chat 产物（done.proposals）→ 流式气泡卸载并展开待确认卡', async () => {
    const { result } = setup()
    const proposal = pendingProposal()

    await act(async () => {
      const p = result.current.submitInput('先看看这个月花了多少，然后记一笔午饭12块')
      await flush()
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
      await flush()
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
      await flush()
      emit('delta', { text: '我正在', channel: 'content', step: 1 })
      emit('error', { code: 'agent_failed', message: '本轮失败' })
      await p
    })

    expect(result.current.streaming).toBeNull()
    expect(result.current.phase).toBe('idle')
    const last = result.current.messages[result.current.messages.length - 1]
    // T-M7-03e：失败 / 断连的文案改为「连接中断」（R-PROD-19 要求明确提示中断状态），
    // 并配一个重连入口（见下方「中断态」一组用例）
    expect(last.text).toBe('我正在\n\n⚠️ 连接中断，本轮未完成')
    expect(result.current.interrupted).toEqual({ input: '你好' })
  })

  it('桥层拒绝（旧 APK 无该命令）→ 同样按失败收尾，不静默回落', async () => {
    mockBridge.runAgent.mockRejectedValue(new Error('unknown command'))
    const { result } = setup()

    await act(async () => { await result.current.submitInput('你好') })

    expect(result.current.phase).toBe('idle')
    const last = result.current.messages[result.current.messages.length - 1]
    expect(last.text).toBe('⚠️ 连接中断，本轮未完成')
    // 不静默回落单发：用户看得见失败，而不是被悄悄换一条链路
    expect(mockBridge.interpretAI).not.toHaveBeenCalled()
  })

  it('done 先到、error 后到（或反之）只收尾一次', async () => {
    const { result } = setup()

    await act(async () => {
      const p = result.current.submitInput('你好')
      await flush()
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
      await flush()
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
      await flush()
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
      await flush()
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

  // ─── T18-3（REQ-67②）：执行凭据（卡上「已执行」的唯一依据）───

  /** 提交一句走 agent、收尾时挂起一张 v2 提案——返回结果供后续点「确认」。 */
  async function submitPendingV2(result: { current: { submitInput: (t: string) => Promise<void> } },
    proposal: ActionProposalV2) {
    await act(async () => {
      const p = result.current.submitInput('把祖传牛肉面那笔的金额改成15块')
      // T18-8 之后 submitInput 先 `await isCloudReachable(bridge)` 再开流——等一个宏任务
      // （本文件的 `flush`），否则 `emit('done')` 会赶在订阅注册之前，帧直接丢掉
      await flush()
      emit('done', donePayload({
        steps: 2, tool_calls: 2, writes: 1,
        proposals: [proposal as unknown as Record<string, unknown>],
      }))
      await p
    })
  }

  it('v2 提案确认后：消息带上服务端**执行凭据**（回执里的实体 id）', async () => {
    const { result } = setup()
    const proposal = pendingV2Proposal()
    mockBridge.executeProposal.mockResolvedValue({
      transaction_ids: ['tx-9'], proposal_id: proposal.proposalId, undo_window_seconds: 5,
      // T20-1（REQ-77）：回执还带回了 op 名与「执行前」的快照——凭据原样收下，
      // 卡的分型与新旧对照都从这里取（见 `ExecutionEvidence`）。
      before: { amountCents: 1200, note: '祖传牛肉面' },
    })

    await submitPendingV2(result, proposal)
    expect(result.current.cardStatus).toBe('pending-confirm')
    // 还没确认 → 没有任何凭据（卡因此报「未执行 · 待确认」）
    expect(result.current.messages.find(m => m.proposal?.proposalId === proposal.proposalId)
      ?.executions).toBeUndefined()

    await act(async () => { await result.current.editProposal(proposal) })

    expect(mockBridge.executeProposal).toHaveBeenCalledTimes(1)
    const msg = result.current.messages.find(m => m.proposal?.proposalId === proposal.proposalId)
    expect(msg?.proposalStatus).toBe('committed')
    expect(msg?.executions).toEqual([{
      lane: 'server', entityIds: ['tx-9'],
      action: 'transaction_update',
      before: { amountCents: 1200, note: '祖传牛肉面' },
    }])
  })

  it('设备执行 op：凭据记在**设备**这一笔上，且不调 /ai/execute（服务端没有它的落点）', async () => {
    const { result } = setup()
    const proposal = pendingV2Proposal('sync_trigger')

    await submitPendingV2(result, proposal)
    await act(async () => { await result.current.editProposal(proposal) })

    expect(mockBridge.triggerSync).toHaveBeenCalledTimes(1)
    expect(mockBridge.executeProposal).not.toHaveBeenCalled()
    const msg = result.current.messages.find(m => m.proposal?.proposalId === proposal.proposalId)
    expect(msg?.executions).toEqual([{ lane: 'device', entityIds: [] }])
  })

  it('执行失败：**一条凭据都不写**——卡因此不会说「已执行」', async () => {
    const { result } = setup()
    const proposal = pendingV2Proposal()
    mockBridge.executeProposal.mockRejectedValue(new Error('HTTP 500 服务不可用'))

    await submitPendingV2(result, proposal)
    await act(async () => { await result.current.editProposal(proposal) })

    const msg = result.current.messages.find(m => m.proposal?.proposalId === proposal.proposalId)
    expect(msg?.proposalStatus).toBe('error')
    expect(msg?.executions).toBeUndefined()
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
      await flush()
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
      await flush()
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

  // ─── T20-1（REQ-77）：自动执行也留卡 + 回执诚实化（R-PROD-50）───

  /** T20-1：自动执行那轮的回执形状（服务端 `execute_proposal._execute_receipt`）。 */
  function executedReceipt(over: Record<string, unknown> = {}): Record<string, unknown> {
    return {
      proposal_id: 'agent-add-1', transaction_ids: ['tx-30'], undo_window_seconds: 5,
      action: 'transaction_add', message: '记录一笔 支出 ¥30.00（餐饮）',
      payload: { transactions: [{ type: 'expense', amountCents: 3000, categoryId: 'cat-1' }] },
      before: null,
      ...over,
    }
  }

  /** 跑一轮「服务端自动执行」的回合（done.executions 非空那条路径）。 */
  async function runAutoExecuted(
    result: { current: { submitInput: (t: string) => Promise<void> } },
    executions: Array<Record<string, unknown>>, text = '',
  ) {
    await act(async () => {
      const p = result.current.submitInput('记一笔午饭30块')
      await flush()
      if (text) emit('delta', { text, channel: 'content', step: 1 })
      emit('done', donePayload({ tool_calls: 1, writes: 1, executions }))
      await p
    })
  }

  it('自动执行 → 消息上带 v2 提案（历史卡的判据），不再只剩一行文本', async () => {
    const { result } = setup()
    await runAutoExecuted(result, [executedReceipt()], '记好了')

    const last = lastMsg(result)
    expect(last.text).toBe('记好了\n\n已记录 1 笔交易')
    // 历史卡的判据是消息上的 `proposal`。改前这条分支**不带它**——单 Step + 高置信的
    // 记账（最常见的形态）因此在聊天记录里没有卡，切页重进就只剩一行字。
    expect(last.proposal !== undefined && isV2Proposal(last.proposal)).toBe(true)
    expect(last.proposal?.action).toBe('transaction_add')
    // 已经执行完了：这是**回执**不是安全门，没有可确认的东西
    expect((last.proposal as ActionProposalV2).requiresConfirmation).toBe(false)
    expect(last.proposalStatus).toBe('committed')
    // T18-3 口径：卡上「已执行」只认凭据（此前自动执行这轮根本没有凭据）
    expect(last.executions).toEqual([{
      lane: 'server', entityIds: ['tx-30'], action: 'transaction_add', before: null,
    }])
    expect(last.transactionIds).toEqual(['tx-30'])
  })

  it('非记账 op → 回执用服务端 message，不再谎报「已记录 N 笔交易」（R-PROD-50）', async () => {
    const { result } = setup()
    await runAutoExecuted(result, [executedReceipt({
      proposal_id: 'agent-cat-1', transaction_ids: ['cat-1'],
      action: 'category_update', message: '把「交通」的图标改成 🚗',
      payload: { categoryId: 'cat-1', icon: '🚗' }, before: { name: '交通', icon: null },
    })])

    const last = lastMsg(result)
    expect(last.text).toBe('把「交通」的图标改成 🚗')
    expect(isV2Proposal(last.proposal!)).toBe(true)
    // 撤销窗**不为它赋值**（决策 #6）：它不是记账，没有「撤销这笔交易」这回事
    expect(last.transactionIds).toBeUndefined()
  })

  it('撤销窗只对记账开：非记账 op 的实体 id 不进撤销窗（R-PROD-50）', async () => {
    const { result } = setup()
    await runAutoExecuted(result, [executedReceipt()])
    await runAutoExecuted(result, [executedReceipt({
      proposal_id: 'agent-cat-1', transaction_ids: ['cat-1'],
      action: 'category_update', message: '把「交通」的图标改成 🚗',
      payload: { categoryId: 'cat-1', icon: '🚗' },
    })])

    await act(async () => { await result.current.undoExecution() })

    // 撤的是上一次**记账**那笔；分类 id 从未进过撤销窗（改前会拿 cat-1 去删交易）
    expect(mockBridge.softDeleteTransaction).toHaveBeenCalledWith('tx-30', expect.any(String))
    expect(mockBridge.softDeleteTransaction).not.toHaveBeenCalledWith('cat-1', expect.any(String))
  })

  it('一轮多次执行 → 每条一张卡（模型正文只挂第一条，不重复）', async () => {
    const { result } = setup()
    await runAutoExecuted(result, [
      executedReceipt(),
      executedReceipt({ proposal_id: 'agent-add-2', transaction_ids: ['tx-31'] }),
    ], '记好了')

    const cards = result.current.messages.filter(m => m.proposal)
    expect(cards).toHaveLength(2)
    expect(cards[0].text).toBe('记好了\n\n已记录 1 笔交易')
    expect(cards[1].text).toBe('已记录 1 笔交易')
    expect(cards.map(m => m.proposal?.proposalId)).toEqual(['agent-add-1', 'agent-add-2'])
    // 两笔都在撤销窗里（同一个 Turn 记的两笔，撤销要把它们一起撤掉）
    expect(cards[0].transactionIds).toEqual(['tx-30'])
    expect(cards[1].transactionIds).toEqual(['tx-31'])
  })

  // ─── 会话 id 提升到 store ───

  it('done 交回的 conversation_id 落进 store（不再只活在内存 ref）', async () => {
    const { result } = setup()

    await act(async () => {
      const p = result.current.submitInput('你好')
      await flush()
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
      await flush()
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
      await flush()
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
      await flush()
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
      await flush()
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
      await flush()
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
      await flush()
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
      await flush()
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
      await flush()
      emitAll([['error', { code: 'network', message: 'Network error' }]])
      await p
    })

    expect(useAssistantChatStore.getState().activeConversationId).toBe('c-ok')
    expect(lastMsg(result).text).toContain('本轮未完成')
  })
})

/**
 * T-M7-03e：确认门的**四值**（架构 §7.4）+ 中断态。
 *
 * 服务端 loop 只产出「挂起提案」，审批回答落在客户端。四值在这里各有落点：
 *   allowed-once（卡上确认，唯一放行值，执行后即失效）/ rejected（拒绝，零写、任务继续）/
 *   cancelled（停止生成 / 语音打断，零写、任务中止）/ unavailable（无法取得授权 → 视为拒绝、零写）。
 *
 * 两条设计边界在本组用例里被钉住：
 * ① **事件驱动但动作在收尾后启用**——`confirm` 帧到达即出卡（用户提前看见 agent 的意图），
 *    可 `done.proposals` 才是权威清单：流式期间点确认被拒，不在清单上的提案按
 *    `unavailable` 收敛（延续 02i 决策 ③「不给中途提案开写入口」）。
 * ② **取消（主动）与断连（被动）不合并**——前者「已取消」，后者「连接中断」+ 重连入口。
 */
describe('use-assistant — 确认门四值（T-M7-03e）', () => {
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

  const TURN_INPUT = '先看看这个月花了多少，然后记一笔午饭12块'

  /** 起一轮并把一张挂起提案推到客户端（`confirm` 帧）——返回那张提案。 */
  async function openApproval(
    r: { current: { submitInput: (t: string) => Promise<void> } },
    proposal: ActionProposal = pendingProposal(),
  ): Promise<ActionProposal> {
    await act(async () => { await r.current.submitInput(TURN_INPUT) })
    await act(async () => {
      emit('confirm', { step: 2, proposal: proposal as unknown as Record<string, unknown> })
    })
    return proposal
  }

  it('事件驱动：confirm 帧到达即出卡（流式期间就在，不等 done），且**零写**', async () => {
    const { result } = setup()
    const proposal = await openApproval(result)

    // 卡随事件出现——用户提前看见 agent 想做什么（架构 §7.4：审批卡同时是纠偏点）
    expect(result.current.streaming).not.toBeNull()
    expect(result.current.cardStatus).toBe('pending-confirm')
    expect(result.current.activeProposal?.proposalId).toBe(proposal.proposalId)
    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()

    // 收尾：权威清单里有它 → 卡留在屏幕上，可确认
    await act(async () => {
      emit('done', donePayload({ proposals: [proposal as unknown as Record<string, unknown>] }))
    })
    expect(result.current.cardStatus).toBe('pending-confirm')
    expect(result.current.interrupted).toBeNull()
  })

  /**
   * T20-4（REQ-78 上层）：`confirm` 帧的**理由码**要活到消息上。
   *
   * 卡自己不持有它（`activeProposal` 只是个提案对象），页面是**按 proposalId 从消息
   * 取回**的（与 `activeExecutions` 同一条路）——所以这一格没落到消息上，卡上那行
   * 「为什么问我」就是空的。这里钉住的是这条链路的**第一段**。
   */
  it('confirm 帧的理由码落到消息上（卡上「为什么问我」的来源）', async () => {
    const { result } = setup()
    await act(async () => { await result.current.submitInput(TURN_INPUT) })
    const proposal = pendingProposal()
    await act(async () => {
      emit('confirm', {
        step: 1, proposal: proposal as unknown as Record<string, unknown>,
        confirmReason: 'medium_risk',
      })
    })
    expect(lastMsg(result).proposal?.proposalId).toBe(proposal.proposalId)
    expect(lastMsg(result).confirmReason).toBe('medium_risk')
  })

  it('帧里没有理由码 → 消息上记 null（设备 op / 没人可问那一档，卡片不显示理由）', async () => {
    const { result } = setup()
    await act(async () => { await result.current.submitInput(TURN_INPUT) })
    const proposal = pendingProposal()
    await act(async () => {
      emit('confirm', { step: 1, proposal: proposal as unknown as Record<string, unknown> })
    })
    expect(lastMsg(result).confirmReason).toBeNull()
  })

  it('动作只在收尾后启用：流式期间点确认被拒（零写 + 明确提示）', async () => {
    const { result } = setup()
    const proposal = await openApproval(result)

    await act(async () => { await result.current.editProposal(proposal) })

    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
    expect(lastMsg(result).text).toContain('本轮还在执行')
    // 卡还挂着（没被这次无效点击弄丢）
    expect(result.current.cardStatus).toBe('pending-confirm')
  })

  it('rejected：拒绝 → **零写**、卡不悬挂、有回执（任务不因此中止）', async () => {
    const { result } = setup()
    const proposal = await openApproval(result)
    await act(async () => {
      emit('done', donePayload({ proposals: [proposal as unknown as Record<string, unknown>] }))
    })
    expect(result.current.cardStatus).toBe('pending-confirm')

    await act(async () => { result.current.rejectApproval() })

    expect(result.current.activeProposal).toBeNull()
    expect(result.current.cardStatus).toBeNull()
    expect(lastMsg(result).text).toBe('已拒绝这次改动，没有记录')
    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
  })

  it('cancelled：流式期间挂着确认卡时取消 → 卡收敛、零写（不变式 3：不留悬挂确认卡）', async () => {
    const { result } = setup()
    await openApproval(result)
    expect(result.current.cardStatus).toBe('pending-confirm')

    await act(async () => { await result.current.cancelStream() })

    expect(mockBridge.cancelAgent).toHaveBeenCalledTimes(1)
    expect(result.current.activeProposal).toBeNull()
    expect(result.current.cardStatus).toBeNull()
    expect(result.current.streaming).toBeNull()
    expect(lastMsg(result).text).toBe('已取消')
    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
  })

  it('unavailable：中途来的提案不在 done 权威清单里 → **视为拒绝、零写**', async () => {
    const { result } = setup()
    await openApproval(result)
    expect(result.current.cardStatus).toBe('pending-confirm')

    // 服务端最终**没有**挂起它（权威清单为空）——中途的实时信号不作数
    await act(async () => { emit('done', donePayload({ proposals: [] })) })

    expect(result.current.activeProposal).toBeNull()
    expect(result.current.cardStatus).toBeNull()
    expect(result.current.messages.some(m => m.text.includes('未取得确认'))).toBe(true)
    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
  })

  it('断连（被动中断）：挂着的卡按 unavailable 收敛 + 重连入口（R-PROD-19）', async () => {
    const { result } = setup()
    await openApproval(result)

    await act(async () => {
      emitAll([['error', { code: 'agent_failed', message: '流中断' }]])
    })

    // ① 未回答的挂起提案视为拒绝、零写；② 中断提示与重连入口就位
    expect(result.current.activeProposal).toBeNull()
    expect(result.current.cardStatus).toBeNull()
    expect(result.current.interrupted).toEqual({ input: TURN_INPUT })
    expect(lastMsg(result).text).toContain('连接中断')
    expect(lastMsg(result).text).toContain('未确认的改动没有记录')
    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
    // 与取消**不合并**：中断不是「已取消」
    expect(lastMsg(result).text).not.toContain('已取消')
  })

  it('重连入口：把中断那一轮的输入原样重发，提示随之消失', async () => {
    const { result } = setup()
    await act(async () => {
      const p = result.current.submitInput('帮我记一笔午饭12块')
      await flush()
      emitAll([['error', { code: 'agent_failed', message: 'x' }]])
      await p
    })
    expect(result.current.interrupted).toEqual({ input: '帮我记一笔午饭12块' })

    await act(async () => {
      result.current.retryInterrupted()
      await flush()
    })

    expect(result.current.interrupted).toBeNull()
    expect(mockBridge.runAgent).toHaveBeenCalledTimes(2)
    expect(mockBridge.runAgent).toHaveBeenLastCalledWith('帮我记一笔午饭12块', 'book-1', null)
  })

  it('多提案：第二张顶上时**消息流里也有它**（回执与历史卡都挂在消息上）', async () => {
    const p1 = pendingProposal()
    const p2 = { ...pendingProposal(), proposalId: 'agent-def456', message: '再记一笔打车' }
    const { result } = setup()

    await act(async () => {
      const p = result.current.submitInput('两个改动')
      await flush()
      emit('confirm', { step: 2, proposal: p1 as unknown as Record<string, unknown> })
      emit('confirm', { step: 3, proposal: p2 as unknown as Record<string, unknown> })
      emit('done', donePayload({
        proposals: [p1, p2] as unknown as Array<Record<string, unknown>>,
      }))
      await p
    })

    await act(async () => { await result.current.editProposal(p1) })
    await waitFor(() => expect(result.current.activeProposal?.proposalId).toBe('agent-def456'))

    // 第二张在消息流里也有一条——否则写入照成、用户却看不到回执，历史卡也没有它
    expect(result.current.messages.some(m => m.proposal?.proposalId === 'agent-def456')).toBe(true)

    await act(async () => { await result.current.editProposal(p2) })

    const m2 = result.current.messages.find(m => m.proposal?.proposalId === 'agent-def456')
    expect(m2?.text).toBe('已记录 1 笔交易')
    expect(m2?.proposalStatus).toBe('committed')
  })

  it('多提案队列：确认第一张后第二张顶上（一轮可以挂起多笔写）', async () => {
    const p1 = pendingProposal()
    const p2 = { ...pendingProposal(), proposalId: 'agent-def456', message: '再记一笔打车' }
    const { result } = setup()

    await act(async () => {
      const p = result.current.submitInput('先看预算，再记午饭，再记打车')
      await flush()
      emit('confirm', { step: 2, proposal: p1 as unknown as Record<string, unknown> })
      emit('confirm', { step: 3, proposal: p2 as unknown as Record<string, unknown> })
      emit('done', donePayload({
        proposals: [p1, p2] as unknown as Array<Record<string, unknown>>,
      }))
      await p
    })

    // 一次只展示一张（队头）
    expect(result.current.activeProposal?.proposalId).toBe('agent-abc123')

    await act(async () => { await result.current.editProposal(p1) })

    // 第一张写完 → 第二张顶上，仍然是**未写库**的待确认态
    await waitFor(() => expect(result.current.activeProposal?.proposalId).toBe('agent-def456'))
    expect(result.current.cardStatus).toBe('pending-confirm')
    expect(mockBridge.insertTransaction).toHaveBeenCalledTimes(1)
  })

  it('拒绝第一张 → 第二张顶上，且拒绝过的那张不会被后续帧复活', async () => {
    const p1 = pendingProposal()
    const p2 = { ...pendingProposal(), proposalId: 'agent-def456' }
    const { result } = setup()

    await act(async () => {
      const p = result.current.submitInput('两个改动')
      await flush()
      emit('confirm', { step: 2, proposal: p1 as unknown as Record<string, unknown> })
      emit('confirm', { step: 3, proposal: p2 as unknown as Record<string, unknown> })
      emit('done', donePayload({
        proposals: [p1, p2] as unknown as Array<Record<string, unknown>>,
      }))
      await p
    })

    await act(async () => { result.current.rejectApproval() })
    expect(result.current.activeProposal?.proposalId).toBe('agent-def456')

    // 重复的 confirm 帧 / 权威清单里仍有它——已处置的提案都不再成卡
    await act(async () => {
      emit('confirm', { step: 2, proposal: p1 as unknown as Record<string, unknown> })
    })
    expect(result.current.activeProposal?.proposalId).toBe('agent-def456')
  })

  it('拒绝后同一张不会被「权威清单」补回（已处置的提案不再成卡）', async () => {
    const { result } = setup()
    // 流式期间就拒绝（零写动作，不必等收尾）——拒的是「这一项」，不是整个任务
    const proposal = await openApproval(result)
    await act(async () => { result.current.rejectApproval() })
    expect(result.current.activeProposal).toBeNull()

    // 收尾：服务端不知道用户已经拒绝过，`done.proposals` 里仍列着它——
    // 权威清单会**补进**队列里缺的提案（confirm 帧丢了也不漏），但已处置的必须跳过
    await act(async () => {
      emit('done', donePayload({ proposals: [proposal as unknown as Record<string, unknown>] }))
    })
    expect(result.current.activeProposal).toBeNull()
    expect(result.current.cardStatus).toBeNull()
    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
  })

  it('新输入接管会话：未决的确认卡与中断提示一并作废（零写）', async () => {
    const { result } = setup()
    const proposal = await openApproval(result)
    await act(async () => {
      emit('done', donePayload({ proposals: [proposal as unknown as Record<string, unknown>] }))
    })
    expect(result.current.cardStatus).toBe('pending-confirm')

    // 一轮收尾后用户改主意，直接说别的——那张没被回答的卡随之作废（它本来就没写库）
    await act(async () => { await result.current.submitInput('换个说法再说一次') })

    expect(result.current.activeProposal).toBeNull()
    expect(result.current.cardStatus).toBeNull()
    expect(result.current.interrupted).toBeNull()
  })
})

/**
 * T-M7-04b：**断连重连续跑**（客户端一半）。
 *
 * 服务端自本卡起**逐事件落库**（每个配对组一写一提交），所以断连不再等于「这一轮
 * 什么都没留下」。客户端在失败收尾**之前**按游标（上一轮 `done.seq`）续读一次
 * `conversation.events?after_seq=`，把补得到的正文落在中断提示之前。
 *
 * 三条边界各有用例：首轮没有游标（退回全量拉，用失败帧里的 `conversation_id`）、
 * 桥不支持或续读失败（退化成「什么都不补」，中断提示照旧）、主动取消（不续读——
 * 用户是自己走的，服务端那半边也没打算续）。
 *
 * 桥的 `fetchConversationEvents` **不在共享 mock 的默认能力里**（与真实能力探测
 * 同形）：本组用例在自己的 `beforeEach` 里装它，因此其余用例覆盖的正是「桥没有这项
 * 能力」这一支。
 */
describe('use-assistant — 断连续跑（T-M7-04b）', () => {
  /** 本组装上的续读能力。共享 mock 上的那一项是**可选的**（与真实能力探测同形），
   *  直接点会撞 TS18048，故在这里用局部变量持有那份 mock。 */
  let fetchEvents: ReturnType<typeof vi.fn>

  const ASSISTANT_EVENT_TURN2 = {
    seq: 9, turn: 2, type: 'assistant',
    data: { role: 'assistant', content: '这一步我查完了', reasoning_content: '' },
    created_at: '2026-09-19T00:00:00Z',
  }

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
    fetchEvents = vi.fn().mockResolvedValue({ conversation_id: 'conv-1', events: [] })
    mockBridge.fetchConversationEvents = fetchEvents
  })

  /** 跑一轮并让它正常收尾——游标（会话 id + seq）在这一轮之后才存在。 */
  async function completeTurn(result: { current: { submitInput: (t: string) => Promise<void> } }, input: string) {
    await act(async () => {
      const p = result.current.submitInput(input)
      await flush()
      emit('done', donePayload({ conversation_id: 'conv-1', seq: 7 }))
      await p
    })
  }

  /** 跑一轮、在中途断连。`extra` 可带 `conversation_id`（首轮断连时的唯一线索）。 */
  async function interruptedTurn(
    result: { current: { submitInput: (t: string) => Promise<void> } },
    input: string,
    extra: Record<string, unknown> = {},
  ) {
    await act(async () => {
      const p = result.current.submitInput(input)
      await flush()
      emitAll([['error', { code: 'ai_unavailable', message: '断了', ...extra }]])
      await p
    })
  }

  it('按上一轮 done.seq 续读，补记排在中断提示之前', async () => {
    const { result } = setup()
    await completeTurn(result, '第一句')
    fetchEvents.mockResolvedValue({
      conversation_id: 'conv-1', events: [ASSISTANT_EVENT_TURN2],
    })

    await interruptedTurn(result, '第二句')

    // 游标就是上一轮 done.seq（严格大于：只取它之后落下的部分）
    expect(fetchEvents).toHaveBeenCalledWith('conv-1', 7)
    const texts = result.current.messages.map(m => m.text)
    expect(texts[texts.length - 2]).toBe('这一步我查完了')
    expect(texts[texts.length - 1]).toContain('连接中断')
  })

  it('首轮断连：没有游标 → 全量拉（after_seq=0），用失败帧里的 conversation_id', async () => {
    const { result } = setup()
    fetchEvents.mockResolvedValue({
      conversation_id: 'conv-9',
      events: [{
        seq: 2, turn: 1, type: 'assistant',
        data: { role: 'assistant', content: '完整回答：你好呀', reasoning_content: '' },
        created_at: '2026-09-19T00:00:00Z',
      }],
    })

    await interruptedTurn(result, '你好', { conversation_id: 'conv-9' })

    expect(fetchEvents).toHaveBeenCalledWith('conv-9', 0)
    // 服务端其实写完了这一轮、只是 done 帧没送到客户端——正文比半截气泡长，补回来
    expect(result.current.messages.some(m => m.text === '完整回答：你好呀')).toBe(true)
  })

  it('补记不重复本地已有的正文（半截气泡是所有步骤 delta 的拼接）', async () => {
    const { result } = setup()
    fetchEvents.mockResolvedValue({
      conversation_id: 'conv-1',
      events: [{
        seq: 1, turn: 1, type: 'assistant',
        data: { role: 'assistant', content: '我先查一下。', reasoning_content: '' },
        created_at: '2026-09-19T00:00:00Z',
      }],
    })

    await act(async () => {
      const p = result.current.submitInput('看看流水')
      await flush()
      emit('delta', { text: '我先查一下。', channel: 'content', step: 1 })
      emitAll([['error', { code: 'ai_unavailable', message: '断了', conversation_id: 'conv-1' }]])
      await p
    })

    const assistants = result.current.messages.filter(m => m.role === 'assistant')
    expect(assistants).toHaveLength(1)  // 只有半截 + 提示那一条，没有重影
    expect(assistants[0].text).toBe('我先查一下。\n\n⚠️ 连接中断，本轮未完成')
  })

  it('续读失败 → 退化成「什么都不补」，中断提示照旧（补救不拖垮收尾）', async () => {
    const { result } = setup()
    await completeTurn(result, '第一句')
    fetchEvents.mockRejectedValue(new Error('offline'))

    await interruptedTurn(result, '第二句')

    expect(result.current.interrupted).toEqual({ input: '第二句' })
    expect(lastMsg(result).text).toContain('连接中断')
  })

  it('桥没有续读能力（旧 APK）→ 跳过续读，收尾照常', async () => {
    const { result } = setup()
    const saved = mockBridge.fetchConversationEvents
    ;(mockBridge as unknown as Record<string, unknown>).fetchConversationEvents = undefined
    try {
      await interruptedTurn(result, '你好')

      expect(result.current.interrupted).toEqual({ input: '你好' })
      expect(lastMsg(result).text).toContain('连接中断')
    } finally {
      mockBridge.fetchConversationEvents = saved
    }
  })

  it('主动取消**不续读**——用户是自己走的，那不是断连', async () => {
    const { result } = setup()
    await act(async () => {
      void result.current.submitInput('你好')
      await flush()
    })
    await act(async () => { await result.current.cancelStream() })

    expect(fetchEvents).not.toHaveBeenCalled()
    expect(lastMsg(result).text).toContain('已取消')
  })
})


/**
 * T-M7-04e：**日志游标的落盘路径**。
 *
 * 游标是「本机已并入日志的到哪儿」，`use-conversations` 拿它做跨设备增量补齐。它的
 * 三个写入点在 `use-assistant.settleStream` 里，各对应一种收尾：
 *
 *   · **正常收尾** → `done.seq`（本轮结束的位置）；
 *   · **失败收尾** → 续读拉到的最大 `seq`——**不写就会重影**：失败轮留在日志里的
 *     `user` 事件会在下一次增量补齐时被再并一次；
 *   · **取消** → **抹成未知**（服务端断连取消不写任何日志事件，而本轮已落库的部分
 *     确实在游标之后，推进会漏、不推进会重）。
 */
describe('use-assistant — 日志游标落盘（T-M7-04e）', () => {
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
    mockBridge.fetchConversationEvents = vi.fn()
      .mockResolvedValue({ conversation_id: 'conv-1', events: [] })
    useAssistantChatStore.getState().clear()
  })

  const cursors = () => useAssistantChatStore.getState().cursors

  it('正常收尾：游标写成本轮结束的 seq', async () => {
    const { result } = setup()
    await act(async () => {
      const p = result.current.submitInput('你好')
      await flush()
      emit('done', donePayload({ conversation_id: 'conv-1', seq: 7 }))
      await p
    })

    expect(cursors()['conv-1']).toBe(7)
  })

  it('失败收尾：游标推到续读拉到的最大 seq（否则失败轮的 user 事件会重复并入）', async () => {
    const { result } = setup()
    await act(async () => {
      const p = result.current.submitInput('第一句')
      await flush()
      emit('done', donePayload({ conversation_id: 'conv-1', seq: 7 }))
      await p
    })
    mockBridge.fetchConversationEvents = vi.fn().mockResolvedValue({
      conversation_id: 'conv-1',
      events: [
        { seq: 8, turn: 2, type: 'user', data: { role: 'user', content: '第二句' }, created_at: 'x' },
        { seq: 9, turn: 2, type: 'assistant', data: { role: 'assistant', content: '半句' }, created_at: 'x' },
      ],
    })

    await act(async () => {
      const p = result.current.submitInput('第二句')
      await flush()
      emitAll([['error', { code: 'ai_unavailable', message: '断了' }]])
      await p
    })

    expect(cursors()['conv-1']).toBe(9)
  })

  it('取消：游标抹成未知（本地那半截与服务端日志的位置对不上，宁可下次不做增量）', async () => {
    const { result } = setup()
    await act(async () => {
      const p = result.current.submitInput('第一句')
      await flush()
      emit('done', donePayload({ conversation_id: 'conv-1', seq: 7 }))
      await p
    })
    expect(cursors()['conv-1']).toBe(7)

    await act(async () => {
      void result.current.submitInput('第二句')
      await flush()
    })
    await act(async () => { await result.current.cancelStream() })

    expect(cursors()['conv-1']).toBeUndefined()
  })
})
