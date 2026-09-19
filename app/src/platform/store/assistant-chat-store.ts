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
 *
 * T-M7-03b（P2）：**按会话分桶**，本 store 降级为「投影 B 的客户端缓存」（架构 §6.2
 * ——会话是服务端的事件日志，客户端只缓存 UI 展示那一层投影）。三处变化：
 *
 *   1. `activeConversationId` 落进 store——P1 里它是 use-assistant 的内存 ref
 *      （`conversationIdRef`，切页 remount 即丢，重进就开新会话）；
 *   2. 消息按会话分桶，切换会话互不串扰（03d 的列表/切换调 `switchConversation`）；
 *   3. 持久化形状升到 **v2**，v1 的扁平单会话数据经 `migrate` 迁进本地会话桶，
 *      **不丢一条历史**。
 *
 * 「当前会话的投影」就存在顶层 `messages`（不是 `projections` 里的一条）：
 * 读当前会话因此仍是一次属性访问，`setState({ messages })` 这类既有写法照常成立；
 * `projections` 只放**非活动**会话，由 `switchConversation` 在两处之间搬运。
 */

import { create } from 'zustand'
import { persist, createJSONStorage, type StateStorage } from 'zustand/middleware'
import type { AssistantMessage } from '../../features/assistant/use-assistant'

/** 历史上限：超出弃旧保新（REQ-42 决策 #7）。按**每个会话**各自计。 */
export const MAX_ASSISTANT_MESSAGES = 200

/** 尚未拿到服务端会话 id 时的桶键：首轮 `done` 之前（或全程离线）的消息都归这里。
 *  `adoptConversation` 会把这一桶「改名」成服务端交回的 id。 */
export const LOCAL_CONVERSATION_KEY = '__local__'

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
  /** 当前活动会话 id；null = 本地会话桶（服务端还没交回 id） */
  activeConversationId: string | null
  /** **当前活动会话**的投影 B（消息列表）。写入上限 MAX_ASSISTANT_MESSAGES 条 */
  messages: AssistantMessage[]
  /** **非活动**会话的投影缓存：conversationId → messages。活动会话不在本表里
   *  （不变式由 switchConversation / adoptConversation 维护） */
  projections: Record<string, AssistantMessage[]>
  /** 兼容 React setState 形态（值或 updater），use-assistant 调用点零改动接入 */
  setMessages: (next: AssistantChatSetter) => void
  /** 切换活动会话（03d 的会话列表调它）。当前会话先入缓存、目标会话出缓存 */
  switchConversation: (id: string | null) => void
  /** 首轮 `done.conversation_id` 交回时把当前会话「改名」——投影不搬家，只换键 */
  adoptConversation: (id: string) => void
  /**
   * T-M7-03d：**丢弃已失效的会话 id**（服务端对它的 404 —— 别人的 / 已删除的 /
   * 不存在的一律同码）。
   *
   * 与 `switchConversation(null)` 的区别：**不动消息**。当前这一屏的消息继续显示
   * （它们是用户刚看见的内容），只是不再挂在那个 id 上——于是下一轮不带
   * `conversation_id`，服务端开一个新会话（03b 交接的降级契约的终态）。
   * 若走切换，本地桶的旧内容会把这屏消息顶掉，用户会看到「内容凭空变了」。
   */
  forgetConversation: () => void
  /**
   * T-M7-03d：删除会话后的本地清理。**回退目标是调用方定的确定性目标**
   * （列表里剩下的第一条，或 null = 本地会话），store 只负责执行。
   *
   * - 删的不是当前会话 → 只清它的投影缓存，当前视图不动；
   * - 删的是当前会话 → 丢它的消息，切到 `fallbackId`（null = 本地会话桶）。
   */
  removeConversation: (id: string, fallbackId: string | null) => void
  /** 登出挂钩（SecurityPage.doLogout）：清空**全部**会话与缓存 */
  clear: () => void
}

const cap = (arr: AssistantMessage[]) =>
  arr.length > MAX_ASSISTANT_MESSAGES ? arr.slice(-MAX_ASSISTANT_MESSAGES) : arr

export const useAssistantChatStore = create<AssistantChatState>()(
  persist(
    (set) => ({
      activeConversationId: null,
      messages: [],
      projections: {},
      setMessages: (next) =>
        set((s) => ({ messages: cap(typeof next === 'function' ? next(s.messages) : next) })),
      switchConversation: (id) =>
        set((s) => {
          const from = s.activeConversationId ?? LOCAL_CONVERSATION_KEY
          const to = id ?? LOCAL_CONVERSATION_KEY
          if (from === to) return s
          // 当前会话**无条件**入缓存（哪怕是空表）：切回来必须还是当时的样子
          const projections = { ...s.projections, [from]: cap(s.messages) }
          const next = projections[to] ?? []
          delete projections[to] // 不变式：活动会话不留副本
          return { activeConversationId: id, messages: cap(next), projections }
        }),
      adoptConversation: (id) =>
        set((s) => {
          if (s.activeConversationId === id) return s
          // **只在「本地会话刚拿到服务端 id」时采纳**——已经切到别的会话时（03d 的
          // 会话列表允许在流式期间被点），这一轮的归属已不在当前链上：此时改 id 会把
          // 用户**拽回**旧会话，还会顺手删掉目标会话的缓存。这种情况不动任何状态。
          if (s.activeConversationId !== null) return s
          const projections = { ...s.projections }
          // 不变式修复：本地键此时不该还在缓存里（切回本地会话时已被消费掉）
          delete projections[LOCAL_CONVERSATION_KEY]
          return { activeConversationId: id, projections }
        }),
      clear: () => set({ activeConversationId: null, messages: [], projections: {} }),
      forgetConversation: () =>
        set((s) => {
          const key = s.activeConversationId
          if (key === null) return s
          const projections = { ...s.projections }
          delete projections[key]
          // 本地桶此刻成为活动桶：按不变式（活动会话不留副本）清掉它的缓存，
          // 否则「切回来」会复活一份与当前这屏不一致的旧内容
          delete projections[LOCAL_CONVERSATION_KEY]
          // **messages 原地不动**——见接口注释：这一屏是用户刚看到的内容
          return { activeConversationId: null, projections }
        }),
      removeConversation: (id, fallbackId) =>
        set((s) => {
          const projections = { ...s.projections }
          delete projections[id]
          if (s.activeConversationId !== id) return { projections }
          // 删的是当前会话：它的消息一并丢弃（会话已经不存在了，留着就是幽灵），
          // 切到调用方定好的回退目标——null 即本地会话桶
          const to = fallbackId ?? LOCAL_CONVERSATION_KEY
          const next = projections[to] ?? []
          delete projections[to]
          return { activeConversationId: fallbackId, messages: cap(next), projections }
        }),
    }),
    {
      name: 'aozora-assistant-chat',
      version: 2,
      storage: createJSONStorage(() => safeStorage),
      partialize: (s) => ({
        messages: s.messages,
        projections: s.projections,
        activeConversationId: s.activeConversationId,
      }),
      /**
       * v1 → v2（T-M7-03b）：v1 是**扁平单会话**（落盘只有 `messages`，没有会话维度）。
       * 迁移成「本地会话桶」——`activeConversationId` 保持 null，旧历史照常显示在当前
       * 会话里；用户切到别的会话（03d）之后它才成为一条独立的历史。**不丢消息**。
       */
      migrate: (persisted, version) => {
        if (version < 2) {
          const old = (persisted ?? {}) as { messages?: unknown }
          return {
            activeConversationId: null,
            messages: cap(Array.isArray(old.messages) ? old.messages as AssistantMessage[] : []),
            projections: {},
          }
        }
        return persisted as AssistantChatState
      },
    },
  ),
)