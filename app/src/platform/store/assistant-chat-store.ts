/**
 * T14-4（REQ-42 / 决策 #7）：聊天页对话历史持久化 store。
 *
 * AssistantPage 无 keep-alive，切页 remount 即丢 messages——把 messages 外置到
 * zustand persist（localStorage key 'aozora-assistant-chat'），仿 image-draft-store。
 * 边界（决策 #7）：
 *   - 只存 messages，写入上限 MAX_ASSISTANT_MESSAGES 条（溢出弃旧、保留尾部）；
 *   - processing/undo 窗/pendingOp 等瞬时态不在本 store（留在 use-assistant 内存态）；
 *   - localStorage 读写抛错（quota/隐私模式）静默降级为纯内存，不炸页面；
 *   - 登出时由 SecurityPage.doLogout 调 clear()（防跨账号串会话）。
 */

import { create } from 'zustand'
import { persist, createJSONStorage, type StateStorage } from 'zustand/middleware'
import type { AssistantMessage } from '../../features/assistant/use-assistant'

/** 历史上限：超出弃旧保新（REQ-42 决策 #7） */
export const MAX_ASSISTANT_MESSAGES = 200

/** localStorage 在隐私模式/quota 满时读写抛错——包一层静默失败的 storage（决策 #7） */
const safeStorage: StateStorage = {
  getItem: (name) => {
    try {
      return localStorage.getItem(name)
    } catch {
      return null
    }
  },
  setItem: (name, value) => {
    try {
      localStorage.setItem(name, value)
    } catch { /* 持久化失败 → 本轮仅内存态，下次写入自愈 */ }
  },
  removeItem: (name) => {
    try {
      localStorage.removeItem(name)
    } catch { /* 同上 */ }
  },
}

export type AssistantChatSetter =
  | AssistantMessage[]
  | ((prev: AssistantMessage[]) => AssistantMessage[])

interface AssistantChatState {
  messages: AssistantMessage[]
  /** 兼容 React setState 形态（值或 updater），use-assistant 调用点零改动接入 */
  setMessages: (next: AssistantChatSetter) => void
  clear: () => void
}

const cap = (arr: AssistantMessage[]) =>
  arr.length > MAX_ASSISTANT_MESSAGES ? arr.slice(-MAX_ASSISTANT_MESSAGES) : arr

export const useAssistantChatStore = create<AssistantChatState>()(
  persist(
    (set) => ({
      messages: [],
      setMessages: (next) =>
        set((s) => ({ messages: cap(typeof next === 'function' ? next(s.messages) : next) })),
      clear: () => set({ messages: [] }),
    }),
    {
      name: 'aozora-assistant-chat',
      version: 1,
      storage: createJSONStorage(() => safeStorage),
      partialize: (s) => ({ messages: s.messages }),
    },
  ),
)
