/**
 * T14-4（REQ-42 / 决策 #7）：assistant-chat-store 单测。
 *
 * 覆盖：updater/值双形态写入、200 条截断保尾、clear、持久化 key 与 partialize
 * （只落数据字段）、setItem/getItem 抛错静默容错、模块重载后历史恢复。
 *
 * T-M7-03b（P2）：多会话分桶——`switchConversation` 互不串扰、`adoptConversation`
 * 把本地会话固化到服务端 id、`clear` 清全部桶、**v1 落盘数据迁移**（version bump）。
 *
 * T-M7-03d 收口：`newConversation`（「新会话」）——本地会话让位时铸 `local:<uuid>`
 * 留下、`toServerConversationId` 把本地键翻成 null（不发上服务端）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  useAssistantChatStore, MAX_ASSISTANT_MESSAGES, LOCAL_CONVERSATION_KEY,
  LOCAL_ID_PREFIX, toServerConversationId,
} from '../../platform/store/assistant-chat-store'
import type { AssistantMessage } from '../../features/assistant/use-assistant'

const STORAGE_KEY = 'aozora-assistant-chat'

const msg = (i: number): AssistantMessage => ({ id: `m-${i}`, role: 'user', text: `t-${i}` })
const many = (n: number, start = 0) =>
  Array.from({ length: n }, (_, k) => msg(start + k))

const s = () => useAssistantChatStore.getState()

// store 是模块级单例——每个用例前复位（含会话维度），否则上一条用例的活动会话会串场
beforeEach(() => {
  useAssistantChatStore.getState().clear()
  localStorage.clear()
})
afterEach(() => {
  vi.restoreAllMocks()
})

describe('assistant-chat-store', () => {

  it('setMessages 接受直接数组与 React 风格 updater', () => {
    useAssistantChatStore.getState().setMessages(many(2))
    expect(useAssistantChatStore.getState().messages).toHaveLength(2)

    useAssistantChatStore.getState().setMessages(prev => [...prev, msg(9)])
    const { messages } = useAssistantChatStore.getState()
    expect(messages).toHaveLength(3)
    expect(messages.at(-1)!.id).toBe('m-9')
  })

  it('写入超过 200 条截断：保留最新尾部', () => {
    useAssistantChatStore.getState().setMessages(many(250))
    const { messages } = useAssistantChatStore.getState()
    expect(messages).toHaveLength(MAX_ASSISTANT_MESSAGES)
    expect(messages[0].id).toBe(`m-${250 - MAX_ASSISTANT_MESSAGES}`)
    expect(messages.at(-1)!.id).toBe('m-249')
  })

  it('updater 追加越界同样截断保尾', () => {
    useAssistantChatStore.getState().setMessages(many(MAX_ASSISTANT_MESSAGES))
    useAssistantChatStore.getState().setMessages(prev => [...prev, ...many(5, 200)])
    const { messages } = useAssistantChatStore.getState()
    expect(messages).toHaveLength(MAX_ASSISTANT_MESSAGES)
    expect(messages.at(-1)!.id).toBe('m-204')
    expect(messages[0].id).toBe('m-5')
  })

  it('clear 清空（登出挂钩行为）', () => {
    useAssistantChatStore.getState().setMessages(many(3))
    useAssistantChatStore.getState().clear()
    expect(useAssistantChatStore.getState().messages).toEqual([])
  })

  it('持久化到 localStorage key aozora-assistant-chat，partialize 只落数据字段（v3）', () => {
    useAssistantChatStore.getState().setMessages(many(2))
    const raw = localStorage.getItem(STORAGE_KEY)
    expect(raw).toBeTruthy()
    const parsed = JSON.parse(raw!)
    expect(parsed.state.messages).toHaveLength(2)
    // T-M7-03b：v2 起多会话——会话维度也落盘（否则切页 remount 后退回单会话）
    expect(parsed.state.projections).toEqual({})
    expect(parsed.state.activeConversationId).toBeNull()
    // T-M7-04e：v3 起多一个日志游标（跨设备增量补齐的起点）
    expect(parsed.state.cursors).toEqual({})
    expect(parsed.state.setMessages).toBeUndefined()
    expect(parsed.state.clear).toBeUndefined()
    expect(parsed.state.switchConversation).toBeUndefined()
    expect(parsed.state.setCursor).toBeUndefined()
    expect(parsed.state.forgetCursor).toBeUndefined()
    expect(parsed.version).toBe(3)
  })

  it('setItem 抛错（quota/隐私模式）：写入静默降级为内存态，不抛', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem')
      .mockImplementation(() => { throw new Error('QuotaExceededError') })
    expect(() => useAssistantChatStore.getState().setMessages(many(3))).not.toThrow()
    // 内存态照常可用，仅落盘失败
    expect(useAssistantChatStore.getState().messages).toHaveLength(3)
    expect(() => useAssistantChatStore.getState().clear()).not.toThrow()
    spy.mockRestore()
  })

  it('getItem 抛错：模块重载不阻塞（rehydrate 静默失败，回退空态）', async () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem')
      .mockImplementation(() => { throw new Error('SecurityError') })
    vi.resetModules()
    const mod = await import('../../platform/store/assistant-chat-store')
    spy.mockRestore()
    expect(mod.useAssistantChatStore.getState().messages).toEqual([])
  })

  it('模块重载后从 localStorage 恢复历史（跨会话）', async () => {
    useAssistantChatStore.getState().setMessages(many(3))
    vi.resetModules()
    const mod = await import('../../platform/store/assistant-chat-store')
    const { messages } = mod.useAssistantChatStore.getState()
    expect(messages).toHaveLength(3)
    expect(messages.map(m => m.id)).toEqual(['m-0', 'm-1', 'm-2'])
  })
})

describe('assistant-chat-store — 多会话分桶（T-M7-03b）', () => {
  it('switchConversation：切换会话互不串扰，切回来还是当时的样子', () => {
    s().setMessages(many(2))                       // 本地会话：m-0 / m-1
    s().switchConversation('conv-A')
    expect(s().messages).toEqual([])               // 新会话是空的，不串到本地会话
    s().setMessages(many(2, 10))                   // conv-A：m-10 / m-11

    s().switchConversation(null)                   // 切回本地会话
    expect(s().messages.map(m => m.id)).toEqual(['m-0', 'm-1'])

    s().switchConversation('conv-A')
    expect(s().messages.map(m => m.id)).toEqual(['m-10', 'm-11'])
  })

  it('switchConversation：离开的会话写入缓存，目标会话出缓存、不留副本', () => {
    s().switchConversation('conv-A')
    // 空会话也照样入缓存（否则切回来会「继承」别人的消息）
    expect(s().projections[LOCAL_CONVERSATION_KEY]).toEqual([])
    s().setMessages(many(1, 10))                   // conv-A：m-10
    s().switchConversation('conv-B')

    expect(s().activeConversationId).toBe('conv-B')
    expect(s().messages).toEqual([])
    expect(s().projections['conv-A'].map(m => m.id)).toEqual(['m-10'])
    expect(s().projections['conv-B']).toBeUndefined() // 活动会话不留在缓存里
  })

  it('switchConversation 到同一个会话是 no-op（不把活动会话搬进缓存）', () => {
    s().switchConversation('conv-A')
    s().setMessages(many(1, 10))
    s().switchConversation('conv-A')
    expect(s().messages.map(m => m.id)).toEqual(['m-10'])
    expect(s().projections['conv-A']).toBeUndefined()
  })

  it('adoptConversation：首轮 done 交回 id → 当前投影换键，不搬家、不留副本', () => {
    s().setMessages(many(2))                       // 本地会话的消息
    s().adoptConversation('conv-42')

    expect(s().activeConversationId).toBe('conv-42')
    expect(s().messages.map(m => m.id)).toEqual(['m-0', 'm-1'])
    expect(s().projections[LOCAL_CONVERSATION_KEY]).toBeUndefined()
  })

  it('adoptConversation 重复同一个 id 是 no-op', () => {
    s().adoptConversation('conv-42')
    s().setMessages(many(1))
    s().adoptConversation('conv-42')
    expect(s().messages).toHaveLength(1)
  })

  it('adoptConversation：已经切到别的会话时不采纳（不拽回、不删目标会话的缓存）', () => {
    s().setMessages(many(2))                     // 本地会话：m-0 / m-1
    s().switchConversation('conv-A')             // 切走（本地会话入缓存）
    s().setMessages(many(1, 10))                 // conv-A：m-10

    // 上一轮迟到的 done 交回一个新 id —— 此时用户在 conv-A，不该被拽走
    s().adoptConversation('conv-X')

    expect(s().activeConversationId).toBe('conv-A')
    expect(s().messages.map(m => m.id)).toEqual(['m-10'])
    expect(s().projections[LOCAL_CONVERSATION_KEY].map(m => m.id)).toEqual(['m-0', 'm-1'])
    expect(s().projections['conv-A']).toBeUndefined()
  })

  it('clear（登出挂钩）清空全部会话与缓存', () => {
    s().setMessages(many(2))
    s().switchConversation('conv-A')
    s().setMessages(many(1, 10))

    s().clear()
    expect(s().messages).toEqual([])
    expect(s().projections).toEqual({})
    expect(s().activeConversationId).toBeNull()
  })

  it('会话维度跨模块重载一并恢复（activeConversationId + 其他桶）', async () => {
    s().switchConversation('conv-A')
    s().setMessages(many(1, 10))
    s().switchConversation('conv-B')
    s().setMessages(many(1, 20))

    vi.resetModules()
    const mod = await import('../../platform/store/assistant-chat-store')
    const st = mod.useAssistantChatStore.getState()
    expect(st.activeConversationId).toBe('conv-B')
    expect(st.messages.map(m => m.id)).toEqual(['m-20'])
    expect(st.projections['conv-A'].map(m => m.id)).toEqual(['m-10'])
  })

  it('v1 落盘数据迁移：扁平单会话历史进本地桶，一条不丢', async () => {
    // v1 的实际落盘形状：{ state: { messages }, version: 1 }
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      state: { messages: many(3).map(m => ({ ...m, proposalStatus: 'committed' })) },
      version: 1,
    }))
    vi.resetModules()
    const mod = await import('../../platform/store/assistant-chat-store')
    const st = mod.useAssistantChatStore.getState()

    expect(st.messages.map(m => m.id)).toEqual(['m-0', 'm-1', 'm-2'])
    expect(st.messages[0].proposalStatus).toBe('committed')
    expect(st.activeConversationId).toBeNull()   // 旧数据没有会话维度 → 本地桶
    expect(st.projections).toEqual({})
  })

  it('v1 迁移后继续写入：新消息追加在旧历史之后，落盘升为 v3', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      state: { messages: many(2) }, version: 1,
    }))
    vi.resetModules()
    const mod = await import('../../platform/store/assistant-chat-store')
    mod.useAssistantChatStore.getState().setMessages(prev => [...prev, msg(9)])

    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY)!)
    expect(parsed.version).toBe(3)
    expect(parsed.state.messages.map((m: AssistantMessage) => m.id)).toEqual(['m-0', 'm-1', 'm-9'])
  })

  it('v1 迁移容忍畸形载荷（messages 不是数组）→ 空态，不炸', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ state: { messages: 'oops' }, version: 1 }))
    vi.resetModules()
    const mod = await import('../../platform/store/assistant-chat-store')
    expect(mod.useAssistantChatStore.getState().messages).toEqual([])
  })

  it('v1 迁移同样受 200 条上限约束（弃旧保新）', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      state: { messages: many(260) }, version: 1,
    }))
    vi.resetModules()
    const mod = await import('../../platform/store/assistant-chat-store')
    const { messages } = mod.useAssistantChatStore.getState()
    expect(messages).toHaveLength(MAX_ASSISTANT_MESSAGES)
    expect(messages.at(-1)!.id).toBe('m-259')
  })
})

/**
 * T-M7-03d 收口：「新会话」不能拿 `switchConversation(null)` 顶替——活动桶已经是
 * 本地槽时它是空操作（按了没反应），缓存里还压着一条本地桶时它把旧内容顶上来
 * （「新会话」显示成别的会话）。`newConversation` 把两头都堵上，且**零删除**：
 * 让位的本地会话铸成 `local:<uuid>` 留在缓存里，仍可在列表里切回。
 */
describe('assistant-chat-store — 新会话（T-M7-03d 收口）', () => {
  const localKeys = (p: Record<string, unknown>) =>
    Object.keys(p).filter(k => k.startsWith(LOCAL_ID_PREFIX))

  it('从服务端会话新建：旧会话入缓存，活动槽换成空表', () => {
    s().switchConversation('conv-A')
    s().setMessages(many(3))

    s().newConversation()

    expect(s().activeConversationId).toBeNull()
    expect(s().messages).toEqual([])
    expect(s().projections['conv-A'].map(m => m.id)).toEqual(['m-0', 'm-1', 'm-2'])
  })

  it('本地槽有内容：铸一个本地键留在缓存（不静默清空），活动槽换成空表', () => {
    s().setMessages(many(2))

    s().newConversation()

    expect(s().activeConversationId).toBeNull()
    expect(s().messages).toEqual([])
    const keys = localKeys(s().projections)
    expect(keys).toHaveLength(1)
    expect(s().projections[keys[0]].map(m => m.id)).toEqual(['m-0', 'm-1'])
  })

  it('缓存里压着的那条本地会话也让位（不被顶掉、不与当前那条混起来）', () => {
    s().setMessages(many(2))                    // 本地槽：m-0 / m-1
    s().switchConversation('conv-A')            // 让位 → projections.__local__
    s().setMessages(many(1, 10))                // conv-A：m-10

    s().newConversation()

    expect(s().projections['conv-A'].map(m => m.id)).toEqual(['m-10'])
    expect(s().projections[LOCAL_CONVERSATION_KEY]).toBeUndefined()
    const keys = localKeys(s().projections)
    expect(keys).toHaveLength(1)
    expect(s().projections[keys[0]].map(m => m.id)).toEqual(['m-0', 'm-1'])
  })

  it('空槽上新建：不产生任何缓存条目（没有东西需要保下来）', () => {
    s().newConversation()

    expect(s().activeConversationId).toBeNull()
    expect(s().messages).toEqual([])
    expect(s().projections).toEqual({})
  })

  it('本地键一律不是服务端 id（`/ai/agent` 靠它开新会话）', () => {
    expect(toServerConversationId(null)).toBeNull()
    expect(toServerConversationId(LOCAL_CONVERSATION_KEY)).toBeNull()
    expect(toServerConversationId(LOCAL_ID_PREFIX + 'x')).toBeNull()
    expect(toServerConversationId('6f1c0f2e-0000-4000-8000-000000000001'))
      .toBe('6f1c0f2e-0000-4000-8000-000000000001')
  })
})

/**
 * T-M7-04e：日志游标——「这条投影已覆盖到日志的哪一行」。
 *
 * 它是跨设备增量补齐的起点，因此有三条必须钉住的口径：
 * ① **只前进**（乱序到达的拉取不能把游标往回拨，否则下一批增量会重复取回旧行）；
 * ② **会话没了就一并作废**（删除 / 404 两条路径）；
 * ③ **v2 存量数据一律迁成「未知」**——04e 之前的桶反推不出日志位置，未知即不做增量。
 */
describe('assistant-chat-store — 日志游标（T-M7-04e）', () => {
  it('setCursor 只前进：更小的值不覆盖，相等的值也不重写', () => {
    s().setCursor('conv-A', 8)
    expect(s().cursors['conv-A']).toBe(8)

    s().setCursor('conv-A', 3)      // 迟到的旧批次
    expect(s().cursors['conv-A']).toBe(8)

    s().setCursor('conv-A', 12)
    expect(s().cursors['conv-A']).toBe(12)
  })

  it('forgetCursor 抹成未知；本就没有时不动 state 引用', () => {
    s().setCursor('conv-A', 5)
    s().forgetCursor('conv-A')
    expect(s().cursors['conv-A']).toBeUndefined()

    const before = s().cursors
    s().forgetCursor('conv-A')
    expect(s().cursors).toBe(before)   // 无变化不产生新对象（zustand 的浅比较据此跳过渲染）
  })

  it('clear（登出）把游标一并清掉——它是本地事实，不该跨账号留着', () => {
    s().setCursor('conv-A', 5)
    s().clear()
    expect(s().cursors).toEqual({})
  })

  it('删除会话：游标与投影一起作废；删的不是当前会话时消息不动', () => {
    s().switchConversation('conv-B')
    s().setCursor('conv-B', 3)
    s().setCursor('conv-A', 9)

    s().removeConversation('conv-A', 'conv-B')

    expect(s().cursors['conv-A']).toBeUndefined()
    expect(s().cursors['conv-B']).toBe(3)
    expect(s().activeConversationId).toBe('conv-B')
  })

  it('陈旧 id 的降级（forgetConversation）：游标随 id 一起丢，消息原地保留', () => {
    s().switchConversation('conv-A')
    s().setMessages(many(2))
    s().setCursor('conv-A', 7)

    s().forgetConversation()

    expect(s().activeConversationId).toBeNull()
    expect(s().cursors['conv-A']).toBeUndefined()
    expect(s().messages.map(m => m.id)).toEqual(['m-0', 'm-1'])
  })

  it('v2 存量数据迁到 v3：游标为空（未知），投影一条不动', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      state: { messages: many(1), projections: { 'conv-A': many(2, 10) }, activeConversationId: 'conv-A' },
      version: 2,
    }))
    vi.resetModules()
    const mod = await import('../../platform/store/assistant-chat-store')
    const st = mod.useAssistantChatStore.getState()

    expect(st.cursors).toEqual({})
    expect(st.messages.map(m => m.id)).toEqual(['m-0'])
    expect(st.projections['conv-A'].map(m => m.id)).toEqual(['m-10', 'm-11'])
  })
})
