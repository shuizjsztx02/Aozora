/**
 * T14-4（REQ-42 / 决策 #7）：assistant-chat-store 单测。
 *
 * 覆盖：updater/值双形态写入、200 条截断保尾、clear、持久化 key 与 partialize
 * （只落 messages）、setItem/getItem 抛错静默容错、模块重载后历史恢复。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  useAssistantChatStore, MAX_ASSISTANT_MESSAGES,
} from '../../platform/store/assistant-chat-store'
import type { AssistantMessage } from '../../features/assistant/use-assistant'

const STORAGE_KEY = 'aozora-assistant-chat'

const msg = (i: number): AssistantMessage => ({ id: `m-${i}`, role: 'user', text: `t-${i}` })
const many = (n: number, start = 0) =>
  Array.from({ length: n }, (_, k) => msg(start + k))

describe('assistant-chat-store', () => {
  beforeEach(() => {
    // 先清内存态（setState 会触发 persist 写），再清 localStorage
    useAssistantChatStore.setState({ messages: [] })
    localStorage.clear()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

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

  it('持久化到 localStorage key aozora-assistant-chat，partialize 只落 messages', () => {
    useAssistantChatStore.getState().setMessages(many(2))
    const raw = localStorage.getItem(STORAGE_KEY)
    expect(raw).toBeTruthy()
    const parsed = JSON.parse(raw!)
    expect(parsed.state.messages).toHaveLength(2)
    expect(parsed.state.setMessages).toBeUndefined()
    expect(parsed.state.clear).toBeUndefined()
    expect(parsed.version).toBe(1)
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
