/**
 * T-M7-03b：agent 流事件的**投影**（纯 reducer）测试。
 *
 * 卡面验收「八类事件 → 投影态逐条断言」在此落地：reducer 是纯函数，不经过 React、
 * 不经过桥，所以可以对每类事件、每种畸形载荷给出确定断言。集成面（气泡渲染、
 * 收尾落点、取消收敛、会话分桶）在 `use-assistant-agent-stream.test.ts`。
 *
 * 载荷形状以 `server/src/modules/ai/stream.py` 的字段表为准：
 *   phase{phase,step,reasoning*} / tool_call{tool,step,message} /
 *   tool_result{tool,step,status,code*,message*} / confirm{step,proposal} /
 *   delta{text,channel,step} / done{...} / error{code,message,conversation_id*}
 */
import { describe, it, expect } from 'vitest'
import {
  applyStreamEvent, createStreamProjection, recoveredMessages, type AgentStreamEvent,
} from '../../features/assistant/use-assistant-stream'
import type { AiConversationEventItem } from '../../platform/bridge/bridge-types'

function ev(event: string, data: Record<string, unknown> = {}): AgentStreamEvent {
  return { streamId: 's-1', event: event as AgentStreamEvent['event'], data }
}

/** 依次施加一串事件，返回最终投影。 */
function run(...events: AgentStreamEvent[]) {
  return events.reduce(applyStreamEvent, createStreamProjection())
}

const doneData = {
  conversation_id: 'conv-1', stop_reason: 'completed', steps: 1,
  tool_calls: 0, writes: 0, usage: { prompt_tokens: 1, completion_tokens: 1 },
  proposals: [], executions: [], pending_input: [],
}

describe('agent 流投影 — 八类事件逐类落进投影', () => {
  it('delta：content 进正文、reasoning 进推理通道，两者绝不混', () => {
    const p = run(
      ev('delta', { text: '先想想', channel: 'reasoning', step: 1 }),
      ev('delta', { text: '你好', channel: 'content', step: 1 }),
      ev('delta', { text: '呀', channel: 'content', step: 1 }),
    )
    expect(p.text).toBe('你好呀')
    expect(p.reasoning).toBe('先想想')
    // 交叉断言：正文里没有推理的任何一个字
    expect(p.text).not.toContain('先想想')
  })

  it('delta 多帧按**追加**拼接（一轮几十上百帧，不假设一轮一帧）', () => {
    const p = run(...['你', '好', '呀', '！'].map(t => ev('delta', { text: t, channel: 'content', step: 1 })))
    expect(p.text).toBe('你好呀！')
  })

  it('delta 缺 channel → 按正文处理（服务端只有 content/reasoning 两值）', () => {
    const p = run(ev('delta', { text: '嗯', step: 1 }))
    expect(p.text).toBe('嗯')
    expect(p.reasoning).toBe('')
  })

  it('phase / tool_call / tool_result 按 step 归并成一条步骤视图', () => {
    const p = run(
      ev('phase', { phase: 'thinking', step: 1, reasoning: 'medium' }),
      ev('tool_call', { tool: 'query_budget', step: 1, message: '正在查询预算…' }),
      ev('tool_result', { tool: 'query_budget', step: 1, status: 'ok' }),
    )
    expect(p.steps).toHaveLength(1)
    expect(p.steps[0]).toMatchObject({
      step: 1, phase: 'thinking', reasoning: 'medium',
      tool: 'query_budget', message: '正在查询预算…', status: 'ok',
    })
  })

  it('phase 随步骤推进被覆盖（thinking → tool_calling 是同一个 step 的两次 phase）', () => {
    const p = run(
      ev('phase', { phase: 'thinking', step: 2 }),
      ev('phase', { phase: 'tool_calling', step: 2 }),
    )
    expect(p.steps).toHaveLength(1)
    expect(p.steps[0].phase).toBe('tool_calling')
  })

  it('tool_calling 的 phase 帧不带 reasoning → 不清掉该步已记的档位', () => {
    // server 的 step_started 带 reasoning（档位），tool_started 那一帧不带
    const p = run(
      ev('phase', { phase: 'thinking', step: 1, reasoning: 'deep' }),
      ev('phase', { phase: 'tool_calling', step: 1 }),
    )
    expect(p.steps[0]).toMatchObject({ phase: 'tool_calling', reasoning: 'deep' })
  })

  it('多步各自成条，按到达顺序排列', () => {
    const p = run(
      ev('phase', { phase: 'thinking', step: 1 }),
      ev('tool_call', { tool: 'a', step: 1, message: '第一步' }),
      ev('tool_result', { tool: 'a', step: 1, status: 'ok' }),
      ev('phase', { phase: 'thinking', step: 2 }),
      ev('tool_call', { tool: 'b', step: 2, message: '第二步' }),
    )
    expect(p.steps.map(s => s.step)).toEqual([1, 2])
    expect(p.steps[1]).toMatchObject({ phase: 'thinking', tool: 'b', status: null })
  })

  it('tool_result 失败：status=error 且带安全枚举码（该步是「失败」态）', () => {
    const p = run(
      ev('tool_call', { tool: 'insert_tx', step: 3, message: '正在记账…' }),
      ev('tool_result', { tool: 'insert_tx', step: 3, status: 'error', code: 'book_not_found', message: '账本不存在' }),
    )
    expect(p.steps[0]).toMatchObject({ status: 'error', code: 'book_not_found' })
  })

  it('confirm 原样留存（**实时信号**，非权威清单——权威是 done.proposals）', () => {
    const proposal = { proposalId: 'p-1', action: 'add_tx' }
    const p = run(ev('confirm', { step: 2, proposal }))
    // T20-4（REQ-78）：帧里没带 `confirmReason` → `reason` 记 null（卡片不显示理由），
    // 不是 undefined——「服务端没给理由」与「这条记录没有该字段」在卡上表现相同，
    // 但投影这一层应当把帧的实况如实记下来。
    expect(p.confirmProposals).toEqual([{ step: 2, proposal, reason: null }])
    // 未收尾：confirm 不是收尾信号
    expect(p.settled).toBe(false)
    expect(p.done).toBeNull()
  })

  it('confirm 带上服务端的理由码（T20-4 / REQ-78：卡上「为什么问我」的来源）', () => {
    const p = run(
      ev('confirm', {
        step: 1, proposal: { proposalId: 'p-1', action: 'transaction_delete' },
        confirmReason: 'medium_risk',
      }),
    )
    expect(p.confirmProposals[0].reason).toBe('medium_risk')
  })

  it('confirm 的 reason 只认字符串：畸形载荷记 null（不把非字符串透给卡片）', () => {
    const p = run(
      ev('confirm', {
        step: 1, proposal: { proposalId: 'p-1' }, confirmReason: { code: 'medium_risk' },
      }),
    )
    expect(p.confirmProposals[0].reason).toBeNull()
  })

  it('confirm 多帧累积成队列（一轮多步可以有多张挂起提案）', () => {
    const p = run(
      ev('confirm', { step: 2, proposal: { proposalId: 'p-1' } }),
      ev('confirm', { step: 3, proposal: { proposalId: 'p-2' } }),
    )
    expect(p.confirmProposals.map(c => c.proposal.proposalId)).toEqual(['p-1', 'p-2'])
    expect(p.confirmProposals.map(c => c.step)).toEqual([2, 3])
  })

  it('同一 proposalId 的重复 confirm 只留一条（重发帧不成第二张卡）', () => {
    const p = run(
      ev('confirm', { step: 2, proposal: { proposalId: 'p-1', action: 'add_tx' } }),
      ev('confirm', { step: 2, proposal: { proposalId: 'p-1', action: 'add_tx' } }),
    )
    expect(p.confirmProposals).toHaveLength(1)
  })

  it('confirm 缺 proposal 载荷 → 不成条（不出无主卡片）', () => {
    const p = run(ev('confirm', { step: 2 }))
    expect(p.confirmProposals).toEqual([])
  })

  it('device_request 原样留存（P1 无生产者，但通道帧不丢）', () => {
    const p = run(
      ev('device_request', { tool: 'pick_icon', request_id: 'r-1' }),
      ev('device_request', { tool: 'scan_receipt', request_id: 'r-2' }),
    )
    expect(p.deviceRequests.map(r => r.request_id)).toEqual(['r-1', 'r-2'])
  })

  it('done：settled + 载荷可用', () => {
    const p = run(ev('done', doneData))
    expect(p.settled).toBe(true)
    expect(p.done?.conversation_id).toBe('conv-1')
    expect(p.error).toBeNull()
    expect(p.canceled).toBe(false)
  })

  it('error：settled + 错误码，且**不清空**已收到的正文与推理（架构 §8.5）', () => {
    const p = run(
      ev('delta', { text: '我正在', channel: 'content', step: 1 }),
      ev('delta', { text: '先看看', channel: 'reasoning', step: 1 }),
      ev('error', { code: 'agent_failed', message: '本轮失败' }),
    )
    expect(p.settled).toBe(true)
    expect(p.error).toEqual({
      code: 'agent_failed', message: '本轮失败', conversationId: null,
    })
    expect(p.text).toBe('我正在')
    expect(p.reasoning).toBe('先看看')
    expect(p.done).toBeNull()
  })

  it('error 带上 conversation_id 时一并留下（T-M7-04b：续读的唯一线索）', () => {
    // 首轮断连时客户端手里还没有服务端 id（它是 done 帧给的），而续读必须
    // 指名道姓地要哪个会话——服务端在失败帧里回传它（会话索引前置检查已提交）。
    const p = run(ev('error', {
      code: 'ai_unavailable', message: 'AI service is temporarily unavailable',
      conversation_id: 'conv-9',
    }))
    expect(p.error?.conversationId).toBe('conv-9')
  })
})

describe('agent 流投影 — 收尾幂等与边界', () => {
  it('收尾后到达的帧一律丢弃（done 先到、error 后到）', () => {
    const p = run(
      ev('delta', { text: '答案', channel: 'content', step: 1 }),
      ev('done', doneData),
      ev('error', { code: 'stream_ended', message: '连接关闭' }),
      ev('delta', { text: '不该出现', channel: 'content', step: 1 }),
    )
    expect(p.error).toBeNull()
    expect(p.text).toBe('答案')
  })

  it('error 先到、done 后到同样只认先到的那个', () => {
    const p = run(
      ev('error', { code: 'timeout', message: '' }),
      ev('done', doneData),
    )
    expect(p.done).toBeNull()
    expect(p.error?.code).toBe('timeout')
  })

  it('**不假设每条回复都从 delta 开始**：只有步骤帧的一轮，text 为空但不异常', () => {
    const p = run(
      ev('phase', { phase: 'tool_calling', step: 1 }),
      ev('tool_call', { tool: 'insert_tx', step: 1, message: '正在记账…' }),
      ev('tool_result', { tool: 'insert_tx', step: 1, status: 'ok' }),
      ev('done', { ...doneData, executions: [{ proposal_id: 'p', transaction_ids: ['t-1'] }] }),
    )
    expect(p.text).toBe('')
    expect(p.reasoning).toBe('')
    expect(p.steps[0].status).toBe('ok')
    expect(p.done?.executions).toHaveLength(1)
  })

  it('畸形帧不改投影：text 非字符串 / step 缺失 / 未知事件名', () => {
    const base = run(ev('delta', { text: '正文', channel: 'content', step: 1 }))
    expect(applyStreamEvent(base, ev('delta', { text: 42, channel: 'content', step: 1 }))).toEqual(base)
    expect(applyStreamEvent(base, ev('delta', { channel: 'content', step: 1 }))).toEqual(base)
    // step 缺失的步骤帧整条丢弃（拼不出步骤归属，留着会是一条无主卡片）
    expect(applyStreamEvent(base, ev('tool_call', { tool: 'x' }))).toEqual(base)
    expect(applyStreamEvent(base, ev('routing', { to: 'ledger' }))).toEqual(base)
  })

  it('reducer 是纯函数：不改入参', () => {
    const before = run(ev('delta', { text: '正文', channel: 'content', step: 1 }))
    const snapshot = JSON.parse(JSON.stringify(before))
    const after = applyStreamEvent(before, ev('tool_call', { tool: 'x', step: 1, message: 'm' }))
    expect(before).toEqual(snapshot)
    expect(after).not.toBe(before)
    expect(after.steps).toHaveLength(1)
  })

  it('createStreamProjection 每次都是干净的新投影', () => {
    const a = createStreamProjection()
    const b = createStreamProjection()
    expect(a).toEqual(b)
    expect(a.steps).not.toBe(b.steps) // 不共享数组：一处追加不会污染另一处
  })
})
/**
 * T-M7-04b：断连续读的**裁剪规则**（纯函数）。
 *
 * `recoveredMessages` 决定「服务端日志里的哪些正文值得补进本地消息流」。三条规则
 * 各有用例：只取最后一轮（断连只可能打断那一轮）、只取 assistant 正文（`user` 是
 * 本端自己写的，工具与结构性事件是投影 B 的内部细节）、已在半截气泡里的不重复补。
 */
describe('agent 流投影 — 断连续读的裁剪规则（T-M7-04b）', () => {
  const item = (
    seq: number, turn: number, type: string, data: Record<string, unknown>,
  ): AiConversationEventItem => ({
    seq, turn, type, data, created_at: '2026-09-19T00:00:00Z',
  })
  const assistant = (seq: number, turn: number, content: string) =>
    item(seq, turn, 'assistant', { role: 'assistant', content, reasoning_content: '' })

  it('只取最后一批里的最后一轮（前面的轮次本地早就有）', () => {
    const out = recoveredMessages([
      assistant(2, 1, '第一轮的回答'),
      item(3, 2, 'user', { role: 'user', content: '第二句' }),
      assistant(4, 2, '第二轮的回答'),
    ], '')
    expect(out).toEqual([{ seq: 4, text: '第二轮的回答' }])
  })

  it('只取 assistant 的正文：user / tool / 结构性事件都不补', () => {
    const out = recoveredMessages([
      item(1, 1, 'user', { role: 'user', content: '看看流水' }),
      assistant(2, 1, '我查一下'),
      item(3, 1, 'tool', { role: 'tool', tool_call_id: 'c1', content: '{}' }),
      item(4, 1, 'error', { code: 'ai_unavailable' }),
    ], '')
    expect(out).toEqual([{ seq: 2, text: '我查一下' }])
  })

  it('正文已在半截气泡里的不重复补（本地是所有步骤 delta 的拼接）', () => {
    const out = recoveredMessages([
      assistant(1, 1, '我先查一下。'),
      assistant(2, 1, ''),
    ], '我先查一下。查完了')
    expect(out).toEqual([])
  })

  it('服务端其实写完了这一轮、但 done 帧没送到 → 补记比半截气泡长的那一段', () => {
    const out = recoveredMessages(
      [assistant(1, 1, '这个月还没有设置预算，你可以先设置一个。')],
      '这个月还没有设置预算，你可以先',
    )
    expect(out).toEqual([{ seq: 1, text: '这个月还没有设置预算，你可以先设置一个。' }])
  })

  it('空日志 / 缺 content / 全空白 → 一条都不补', () => {
    expect(recoveredMessages([], '正文')).toEqual([])
    expect(recoveredMessages([item(1, 1, 'assistant', { role: 'assistant' })], '')).toEqual([])
    expect(recoveredMessages([assistant(1, 1, '   \n ')], '')).toEqual([])
  })
})
