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
 *
 * T-M7-03d 收口：**本地会话要有稳定的桶键**。`newConversation` 是「新会话」的唯一
 * 入口——它把当前本地桶（以及缓存里那条本地桶）**铸成 `local:<uuid>`** 留在
 * `projections` 里，再换一个空桶上来。此前「新会话」走 `switchConversation(null)`，
 * 在活动桶已是本地桶时是空操作、在缓存里还压着本地内容时会把旧内容顶上来——
 * 按下去既不新、又可能显示别的会话。`local:*` 键**不是服务端 id**，一律不得出现在
 * `/ai/agent` 的 `conversation_id` 上（见 `toServerConversationId`）。
 *
 * T-M7-04e（P3）：**新增 `cursors`——每个桶覆盖到的日志位置**，跨设备按需重建的起点。
 *
 * 会话正文不做双向同步实体（决策见 `docs/adr/ADR-008-conversation-cross-device.md`）：
 * 投影 B 是服务端事件日志的**派生数据**，本机没缓存时按需重建、有缓存时按游标**增量
 * 补齐尾部**。于是需要记得「本地这一桶已经覆盖到日志的哪一行」——就是 `cursors`。
 *
 * 它是**本地事实**（本机看到哪儿了），不是服务端数据，因此随投影一起落 localStorage、
 * 登出一起清。**缺省 = 未知**，而未知只有一种处置：**不做增量**（宁可少刷新一次，
 * 也不要重复显示一轮）。见 `use-conversations.ts` 的三条刷新规则。
 */

import { create } from 'zustand'
import { persist, createJSONStorage, type StateStorage } from 'zustand/middleware'
import { newUuid } from '../uuid'
import type { AssistantMessage } from '../../features/assistant/use-assistant'

/** 历史上限：超出弃旧保新（REQ-42 决策 #7）。按**每个会话**各自计。 */
export const MAX_ASSISTANT_MESSAGES = 200

/** 尚未拿到服务端会话 id 时的桶键：首轮 `done` 之前（或全程离线）的消息都归这里。
 *  `adoptConversation` 会把这一桶「改名」成服务端交回的 id。 */
export const LOCAL_CONVERSATION_KEY = '__local__'

/** 本地会话（已从活动桶上让位、但从未固化到服务端）的桶键前缀。 */
export const LOCAL_ID_PREFIX = 'local:'

/**
 * 桶键 → 服务端会话 id：**本地键一律 null**。
 *
 * `/ai/agent` 的 `conversation_id` 只认服务端 id——把 `local:*` / `__local__` 发上去
 * 会撞服务端的 UUID 校验（422），而不是「开新会话」。
 */
export function toServerConversationId(id: string | null): string | null {
  if (!id || id === LOCAL_CONVERSATION_KEY || id.startsWith(LOCAL_ID_PREFIX)) return null
  return id
}

/** 桶键是否本地（未固化到服务端）。会话列表据此决定要不要给重命名 / 删除入口。 */
export function isLocalConversationKey(id: string | null): boolean {
  return toServerConversationId(id) === null
}

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
  /**
   * T-M7-04e：桶键 → 这条投影**已覆盖到的日志位置**（事件 `seq`）。**缺省 = 未知**。
   *
   * 写入方只有两处，都是「本机确实看到过日志的这一段」的时刻：一轮正常收尾
   * （`done.seq`）、一次成功的事件拉取（重建 / 增量补齐 / 断连续读）。
   * **取消**会把对应桶的游标**抹掉**（`forgetCursor`）——见该动作的注释。
   */
  cursors: Record<string, number>
  /** 兼容 React setState 形态（值或 updater），use-assistant 调用点零改动接入 */
  setMessages: (next: AssistantChatSetter) => void
  /** 切换活动会话（03d 的会话列表调它）。当前会话先入缓存、目标会话出缓存 */
  switchConversation: (id: string | null) => void
  /**
   * T-M7-03d 收口：**开一条新会话**——「新会话」按钮的唯一入口。
   *
   * 不能拿 `switchConversation(null)` 顶替：活动桶**已经是**本地桶时它是空操作
   * （按了没反应），而缓存里还压着一条本地桶时它会把那份旧内容顶上来（「新会话」
   * 显示成别的会话）。本动作两头都堵上：
   *
   * - 当前会话先落袋（与服务端会话的切换同口径，有内容才存）；
   * - 缓存里那条本地桶也**铸成 `local:<uuid>` 单独留一条**——它马上要变成活动桶，
   *   而活动会话不留副本（`switchConversation` 维护的不变式）。
   *
   * **全程零删除**：旧内容只是换了个桶键，仍可在会话列表里切回——这正是它与
   * 「清空并新建」的分野（后者不可撤销，不该由一个按钮默默执行）。
   */
  newConversation: () => void
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
  /**
   * T-M7-04e：记下这条投影**已覆盖到的日志位置**（增量补齐的起点）。
   *
   * **只前进**：乱序到达的拉取（两次并发刷新）不该把游标往回拨——回拨会让下一批
   * 增量重新取回已经并入的行。
   */
  setCursor: (key: string, seq: number) => void
  /**
   * T-M7-04e：把这条投影的游标**抹成未知**——「本地这一桶的内容与服务端日志的位置
   * 已经对不上，别再拿游标去做增量」。
   *
   * 目前只有一个调用点：**用户取消**。服务端在断连取消时**不写任何日志事件**
   * （`CancelledError` 不被 `except Exception` 捕获，既没有 `error` 事件也没有 `error`
   * 帧），但这一轮**已经落库的部分事件**确实在游标之后；而本机渲染的半截气泡是流的
   * 拼接、不是日志的投影，无法反推它对应到哪一行。此时推进游标会漏、不推进会重——
   * 唯一安全的选择是**放弃增量**（下一次正常收尾会把游标重新写上，自愈）。
   */
  forgetCursor: (key: string) => void
}

const cap = (arr: AssistantMessage[]) =>
  arr.length > MAX_ASSISTANT_MESSAGES ? arr.slice(-MAX_ASSISTANT_MESSAGES) : arr

export const useAssistantChatStore = create<AssistantChatState>()(
  persist(
    (set) => ({
      activeConversationId: null,
      messages: [],
      projections: {},
      cursors: {},
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
      newConversation: () =>
        set((s) => {
          const projections = { ...s.projections }
          // 当前会话先落袋——与服务端会话的切换同口径（有内容才存，空表存了也是空）
          if (s.activeConversationId !== null) {
            if (s.messages.length > 0) projections[s.activeConversationId] = cap(s.messages)
          } else if (s.messages.length > 0) {
            // 未固化的本地会话：铸一个本地键留下，别让它被下一次输入顶掉
            projections[LOCAL_ID_PREFIX + newUuid()] = cap(s.messages)
          }
          // 缓存里那条本地桶同样要让位：它马上就是活动桶
          const cachedLocal = projections[LOCAL_CONVERSATION_KEY]
          if (cachedLocal && cachedLocal.length > 0) {
            projections[LOCAL_ID_PREFIX + newUuid()] = cap(cachedLocal)
          }
          delete projections[LOCAL_CONVERSATION_KEY]
          return { activeConversationId: null, messages: [], projections }
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
      clear: () => set({
        activeConversationId: null, messages: [], projections: {}, cursors: {},
      }),
      setCursor: (key, seq) =>
        set((s) => (seq > (s.cursors[key] ?? 0) ? { cursors: { ...s.cursors, [key]: seq } } : s)),
      forgetCursor: (key) =>
        set((s) => {
          if (s.cursors[key] === undefined) return s
          const cursors = { ...s.cursors }
          delete cursors[key]
          return { cursors }
        }),
      forgetConversation: () =>
        set((s) => {
          const key = s.activeConversationId
          if (key === null) return s
          const projections = { ...s.projections }
          delete projections[key]
          // 本地桶此刻成为活动桶：按不变式（活动会话不留副本）清掉它的缓存，
          // 否则「切回来」会复活一份与当前这屏不一致的旧内容
          delete projections[LOCAL_CONVERSATION_KEY]
          // 游标一并丢掉：这个 id 已经**不是我的会话**了（别人的 / 已删除的 / 不存在的
          // 三者在服务端同一个 404），它的日志位置对本机毫无意义
          const cursors = { ...s.cursors }
          delete cursors[key]
          // **messages 原地不动**——见接口注释：这一屏是用户刚看到的内容
          return { activeConversationId: null, projections, cursors }
        }),
      removeConversation: (id, fallbackId) =>
        set((s) => {
          const projections = { ...s.projections }
          delete projections[id]
          // 会话已经不存在了，它的日志位置也一并作废（同 forgetConversation 的理由）
          const cursors = { ...s.cursors }
          delete cursors[id]
          if (s.activeConversationId !== id) return { projections, cursors }
          // 删的是当前会话：它的消息一并丢弃（会话已经不存在了，留着就是幽灵），
          // 切到调用方定好的回退目标——null 即本地会话桶
          const to = fallbackId ?? LOCAL_CONVERSATION_KEY
          const next = projections[to] ?? []
          delete projections[to]
          return { activeConversationId: fallbackId, messages: cap(next), projections, cursors }
        }),
    }),
    {
      name: 'aozora-assistant-chat',
      version: 3,
      storage: createJSONStorage(() => safeStorage),
      partialize: (s) => ({
        messages: s.messages,
        projections: s.projections,
        activeConversationId: s.activeConversationId,
        cursors: s.cursors,
      }),
      /**
       * v1 → v2（T-M7-03b）：v1 是**扁平单会话**（落盘只有 `messages`，没有会话维度）。
       * 迁移成「本地会话桶」——`activeConversationId` 保持 null，旧历史照常显示在当前
       * 会话里；用户切到别的会话（03d）之后它才成为一条独立的历史。**不丢消息**。
       *
       * v2 → v3（T-M7-04e）：只多一个 `cursors`。**存量桶一律是「游标未知」**——
       * 它们的内容是 04e 之前由流写入的，反推不出对应到日志的哪一行；按
       * `use-conversations.ts` 的规则，未知即不做增量（原样保留，不重复显示）。
       * 该状态会自愈：用户在这条会话上再跑一轮，`done.seq` 就把游标写上了。
       */
      migrate: (persisted, version) => {
        if (version < 2) {
          const old = (persisted ?? {}) as { messages?: unknown }
          return {
            activeConversationId: null,
            messages: cap(Array.isArray(old.messages) ? old.messages as AssistantMessage[] : []),
            projections: {},
            cursors: {},
          }
        }
        const state = (persisted ?? {}) as Partial<AssistantChatState>
        return { ...state, cursors: state.cursors ?? {} } as AssistantChatState
      },
    },
  ),
)