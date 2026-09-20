/**
 * T-M7-04e：**投影 B 的重建**（纯函数）测试。
 *
 * 这是「跨设备看旧会话」的核心变换：服务端事件日志 → 客户端的消息列表。纯函数、
 * 不经过 React 也不经过桥，所以每条取舍都可以单独钉住（见 `history-projection.ts`
 * 的三条取舍）：
 *
 *   · 用户 / 助手正文各产一条消息，**空正文的 `assistant` 不产**（带 `tool_calls`
 *     的中间步骤正文就是空的，产出空气泡只是噪音）；
 *   · `error` 事件产一条中断标注——丢掉的话，被中断那一轮在历史里凭空消失；
 *   · 其余事件（`tool` / `quota` / `approval_*` / `replace` / `device_request` /
 *     `security`）一律不产消息。
 *
 * 集成面（切会话触发重建、增量补齐、游标写入）在 `assistant-conversations.test.tsx`。
 */
import { describe, it, expect } from 'vitest'
import {
  INTERRUPTED_TURN_NOTE, maxSeqOf, projectHistoryFromEvents,
} from '../../features/assistant/history-projection'
import type { AiConversationEventItem } from '../../platform/bridge/bridge-types'

function item(
  seq: number, type: string, data: Record<string, unknown>, turn = 1,
): AiConversationEventItem {
  return { seq, turn, type, data, created_at: '2026-09-19T11:00:00Z' }
}

const user = (seq: number, text: string, turn = 1) =>
  item(seq, 'user', { role: 'user', content: text }, turn)
const assistant = (seq: number, text: string, turn = 1) =>
  item(seq, 'assistant', { role: 'assistant', content: text, reasoning_content: '' }, turn)

describe('投影 B 重建 — 事件 → 消息列表', () => {
  it('用户与助手正文各产一条消息，顺序按 seq', () => {
    const out = projectHistoryFromEvents([
      user(1, '午饭 12.5'),
      assistant(2, '已按 12.5 计入餐饮'),
    ])
    expect(out.map(m => [m.role, m.text])).toEqual([
      ['user', '午饭 12.5'],
      ['assistant', '已按 12.5 计入餐饮'],
    ])
  })

  it('乱序输入按 seq 重排（顺序即渲染顺序，不该由调用方保证）', () => {
    const out = projectHistoryFromEvents([
      assistant(4, '第二句'),
      user(3, '第一句'),
    ])
    expect(out.map(m => m.text)).toEqual(['第一句', '第二句'])
  })

  it('消息 id 由 seq 派生 → 同一批事件重放两次得到同一串 id（重建幂等）', () => {
    const events = [user(7, 'a'), assistant(8, 'b')]
    expect(projectHistoryFromEvents(events).map(m => m.id)).toEqual(['evt_7', 'evt_8'])
    expect(projectHistoryFromEvents(events).map(m => m.id))
      .toEqual(projectHistoryFromEvents(events).map(m => m.id))
  })

  it('空正文的 assistant 不产消息（带 tool_calls 的中间步骤），但同轮的最终回复照常产', () => {
    const out = projectHistoryFromEvents([
      user(1, '查一下本月餐饮'),
      item(2, 'assistant', {
        role: 'assistant', content: '', reasoning_content: '',
        tool_calls: [{ id: 't1', type: 'function', function: { name: 'query_transactions' } }],
      }),
      item(3, 'tool', { role: 'tool', content: '{"total_cents":12345}' }),
      assistant(4, '本月餐饮 123.45 元'),
    ])
    expect(out.map(m => m.text)).toEqual(['查一下本月餐饮', '本月餐饮 123.45 元'])
  })

  it('tool 事件不产消息（它是助手那条的内部细节，渲染上没有独立位置）', () => {
    const out = projectHistoryFromEvents([item(1, 'tool', { role: 'tool', content: '{}' })])
    expect(out).toEqual([])
  })

  it('error 事件产一条中断标注——被中断的那一轮不会凭空消失', () => {
    const out = projectHistoryFromEvents([
      user(1, '午饭 12.5'),
      item(2, 'error', { code: 'agent_failed', message: 'The assistant could not complete this turn' }),
    ])
    expect(out.map(m => [m.role, m.text])).toEqual([
      ['user', '午饭 12.5'],
      ['assistant', INTERRUPTED_TURN_NOTE],
    ])
  })

  it('结构性事件（quota / replace / approval_* / device_request / security）一律不产消息', () => {
    const out = projectHistoryFromEvents([
      user(1, '你好'),
      item(2, 'quota', { provider: 'deepseek', model: 'deepseek-flash', tokens: 120 }),
      item(3, 'replace', { summary: '前面的对话讲的是餐饮记账' }),
      item(4, 'approval_request', { proposalId: 'p-1' }),
      item(5, 'approval_result', { proposalId: 'p-1', approved: true }),
      item(6, 'device_request', { kind: 'camera' }),
      item(7, 'security', { reason: 'cross_user_denied' }),
      assistant(8, '好的'),
    ])
    expect(out.map(m => m.text)).toEqual(['你好', '好的'])
  })

  it('尾部的孤立 user 补一条中断标注（流式中杀进程：服务端只留下一条 user 事件）', () => {
    const out = projectHistoryFromEvents([
      user(1, '午饭 12.5'),
      assistant(2, '已按 12.5 计入餐饮'),
      user(3, '那晚饭呢'),
    ])
    expect(out.map(m => [m.role, m.text])).toEqual([
      ['user', '午饭 12.5'],
      ['assistant', '已按 12.5 计入餐饮'],
      ['user', '那晚饭呢'],
      ['assistant', INTERRUPTED_TURN_NOTE],
    ])
  })

  it('补出的标注 id 由那条 user 派生 → 重建仍然幂等（重放不会长出两条）', () => {
    const events = [user(1, '午饭 12.5')]
    const twice = [projectHistoryFromEvents(events), projectHistoryFromEvents(events)]
    expect(twice[0]!.map(m => m.id)).toEqual(['evt_1', 'evt_1_interrupted'])
    expect(twice[0]!.map(m => m.id)).toEqual(twice[1]!.map(m => m.id))
  })

  it('尾部是 assistant / error 时**不**补标注（有答案或被中断已标注，两者都不该再说一次）', () => {
    expect(projectHistoryFromEvents([user(1, 'a'), assistant(2, 'b')]).map(m => m.text))
      .toEqual(['a', 'b'])
    expect(projectHistoryFromEvents([
      user(1, 'a'), item(2, 'error', { code: 'agent_failed', message: 'x' }),
    ]).map(m => m.text)).toEqual(['a', INTERRUPTED_TURN_NOTE])
  })

  it('畸形载荷不炸：content 缺字段 / 不是字符串 → 不产消息', () => {
    const out = projectHistoryFromEvents([
      item(1, 'assistant', { role: 'assistant' }),
      item(2, 'user', { role: 'user', content: 42 }),
      item(3, 'user', { role: 'user', content: '   ' }),
      assistant(4, '正文'),
    ])
    expect(out.map(m => m.text)).toEqual(['正文'])
  })

  it('空日志 → 空列表（新建的会话就是这样）', () => {
    expect(projectHistoryFromEvents([])).toEqual([])
  })

  it('不产生的消息上没有多余字段（重建只认得正文，不编提案 / 状态）', () => {
    const [m] = projectHistoryFromEvents([user(1, 'a')])
    expect(Object.keys(m).sort()).toEqual(['id', 'role', 'text'])
  })

  it('maxSeqOf 取最大 seq；空批回 0（0 = 没有游标）', () => {
    expect(maxSeqOf([user(3, 'a'), assistant(9, 'b'), user(4, 'c')])).toBe(9)
    expect(maxSeqOf([])).toBe(0)
  })
})