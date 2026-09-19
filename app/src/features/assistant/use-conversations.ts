/**
 * T-M7-03d（P2）：多会话列表的数据面——拉取、新建 / 切换 / 重命名 / 删除，以及
 * 离线 / 未登录的降级。
 *
 * 权威面在服务端（架构 §6.3：服务端持有会话状态，客户端只传 `conversation_id`），
 * 本 hook 只做三件事：
 *
 *   1. **对接 03a 的三个端点**（列表 / 重命名 / 删除）——经 T-M7-03d 新增的桥命令
 *      `conversation.list` / `.rename` / `.delete`。「新建」不走端点：服务端在首轮
 *      不带 `conversation_id` 时自建会话（02f 口径），客户端只是切到本地会话桶。
 *   2. **降级**：断网 / 未登录 / 桥不具备这三条命令时，列表退化为**本机投影缓存**
 *      （`assistant-chat-store` 的桶），新建与写操作禁用，**不抛错、不弹红条**。
 *   3. **确定性回退**：删除当前会话后切到列表里剩下的第一条（服务端按活跃倒序，
 *      即最近活跃的另一条）；一条不剩则切回本地会话桶。store 只管执行，
 *      目标由这里算好传下去（见 `removeConversation`）。
 *
 * **不做会话正文跨设备同步**（归 P3 的 04e）：切换到一个本机没缓存过的会话时，
 * 消息区是空的——`GET /ai/conversations/{id}/events` 有意留在 P3 消费。
 *
 * **流式期间禁止切换**（`busy`）：一轮的输出落在活动会话桶上（`setMessages` 是
 * 扁平的），流中切换会让这一轮的消息落到「切过去之后」的会话里。根治要把一轮绑到
 * 发起时的会话（十几处调用点），那属于后续卡；本卡用「禁止切换」把不变量守住。
 *
 * T-M7-03d 收口——**本机未固化的会话也是列表的一部分**：
 *
 * - 在线列表 = 服务端会话（权威）**＋ 本机未固化的那几条**。后者不在服务端列表里，
 *   不列出来就再也切不回去（离线轮 / 失败轮留下的历史会变成不可达的死数据）。
 *   它们没有 `serverId`，因此不给重命名 / 删除入口。
 * - 「新建」走 store 的 `newConversation`（把当前那条本地会话铸成 `local:<uuid>`
 *   留在列表里，再换一个空槽上来）。早先它用 `switchConversation(null)`：活动桶
 *   已经是本地槽时 `from === to` 直接返回（**按了没反应**），而缓存里还压着一条
 *   本地桶时会把那份旧内容顶上来（「新会话」显示成别的会话）——两种都不是「新建」。
 * - 降级态（离线 / 未登录 / 旧 APK）仍只读本机缓存，且**禁用新建**（卡面口径）。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { AiConversationListResponse, AiConversationSummary } from '../../platform/bridge/bridge-types'
import { useBridge } from '../../platform/hooks/use-bridge'
import { useAssistantChatStore, LOCAL_CONVERSATION_KEY, isLocalConversationKey, toServerConversationId } from '../../platform/store/assistant-chat-store'
import type { AssistantMessage } from './use-assistant'

/**
 * 会话索引的窄接口 + 运行时能力探测（同 02h / 03b 的 `AgentStreamBridge`）：
 * 旧 APK 的命令表里没有这三条，探测为假时**整块降级**，不炸页面。
 */
interface ConversationBridge {
  listConversations(): Promise<AiConversationListResponse>
  renameConversation(id: string, title: string): Promise<AiConversationSummary>
  deleteConversation(id: string): Promise<{ success: boolean }>
}

/** 列表的一行（已归一化：服务端会话与本机未固化的会话都渲染成它）。 */
export interface ConversationRow {
  /** store 的桶键：服务端 id 或本地键（`local:*`）；`null` = 当前那个本地槽 */
  id: string | null
  /** **服务端**会话 id；`null` = 本机尚未固化的会话——没有服务端行，重命名 / 删除
   *  都无从谈起（那两个动作要打端点）。UI 据此决定给不给这两个入口。 */
  serverId: string | null
  title: string
  subtitle: string
  active: boolean
}

/** 本机未固化的会话的副标题：它不在服务端列表里，说明一下为什么没有 ✎ / 🗑。 */
const LOCAL_SUBTITLE = '本机 · 未同步'

/** 降级原因（null = 正常）。三种原因的用户说法在 `DEGRADED_HINT`。 */
export type DegradedReason = 'offline' | 'unauthenticated' | 'unsupported'

export const DEGRADED_HINT: Record<DegradedReason, string> = {
  offline: '离线中，仅显示本机缓存',
  unauthenticated: '未登录，仅显示本机缓存',
  unsupported: '当前版本不支持会话列表，仅显示本机缓存',
}

/** 标题派生：服务端按首轮输入派生标题；本机缓存只能就地取第一条用户消息。 */
function titleFromMessages(messages: AssistantMessage[] | undefined): string {
  const first = messages?.find(m => m.role === 'user' && m.text.trim())
  if (!first) return '本机会话'
  const t = first.text.trim().replace(/\s+/g, ' ')
  return t.length > 18 ? t.slice(0, 18) + '…' : t
}

/** 「09-19 11:00」——列表的副标题（本地缓存没有时间信息时留空）。 */
function shortTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

interface UseConversationsOptions {
  /** 抽屉是否打开——打开才拉列表（关着时不产生请求） */
  open: boolean
  /** 流式进行中：切换与写操作一律禁用（见文件头「流式期间禁止切换」） */
  streaming: boolean
}

export interface UseConversationsResult {
  rows: ConversationRow[]
  loading: boolean
  degraded: DegradedReason | null
  /** 一次操作失败的可关闭提示（降级**不算**错误，走 `degraded`） */
  error: string | null
  /** 流式进行中：UI 据此禁用切换 / 新建 / 重命名 / 删除 */
  busy: boolean
  /** 当前活动会话的桶键（null = 本地槽）。消息区据此区分「新会话」与「这条服务端
   *  会话的消息不在本机」（正文跨设备同步归 P3 的 04e） */
  activeConversationId: string | null
  /** 开一条新会话（当前那条本地会话让位、内容保留，见 store 的 `newConversation`） */
  createConversation: () => void
  /** 切换活动会话；`id` 是**行的 id**（本地槽是 null） */
  selectConversation: (id: string | null) => void
  /** 重命名 —— `id` 必须是**服务端会话 id**（`row.serverId`）：本地会话没有服务端行 */
  renameConversation: (id: string, title: string) => Promise<void>
  /** 删除 —— 同上，`id` 是服务端会话 id */
  deleteConversation: (id: string) => Promise<void>
  dismissError: () => void
}

/** 操作失败的用户可读文案（桥错误码 → 中文；未知码走兜底，不透传英文原文）。 */
const OP_ERROR_ZH: Record<string, string> = {
  auth_lost: '登录已失效，请重新登录',
  not_found: '这条会话已经不存在了',
  network: '网络异常，请稍后重试',
  timeout: '请求超时，请稍后重试',
  '429': '操作过于频繁，请稍后重试',
  '503': '服务暂时不可用，请稍后重试',
  unavailable: '服务暂时不可用，请稍后重试',
}

export function useConversations({
  open, streaming,
}: UseConversationsOptions): UseConversationsResult {
  const bridge = useBridge()
  const activeConversationId = useAssistantChatStore(s => s.activeConversationId)
  const messages = useAssistantChatStore(s => s.messages)
  const projections = useAssistantChatStore(s => s.projections)
  const switchConversation = useAssistantChatStore(s => s.switchConversation)
  const newConversation = useAssistantChatStore(s => s.newConversation)
  const removeConversation = useAssistantChatStore(s => s.removeConversation)

  const [items, setItems] = useState<AiConversationSummary[]>([])
  const [loaded, setLoaded] = useState(false)
  /** 拉取失败的原因（null = 正常）。「桥不支持」与「断网」在渲染期派生，不进 state */
  const [loadFailure, setLoadFailure] = useState<DegradedReason | null>(null)
  /** 一次操作失败的可关闭提示（降级**不算**错误，走 `degraded`） */
  const [error, setError] = useState<string | null>(null)
  /** 关掉抽屉后再打开不该看到上一轮的加载态——用序号丢弃迟到的响应 */
  const loadSeqRef = useRef(0)

  const conversationBridge = bridge as unknown as Partial<ConversationBridge>
  const canUseIndex = typeof conversationBridge.listConversations === 'function' &&
    typeof conversationBridge.renameConversation === 'function' &&
    typeof conversationBridge.deleteConversation === 'function'
  const offline = typeof navigator !== 'undefined' && !navigator.onLine

  /**
   * 降级原因在**渲染期派生**（不落 state）：桥能力与连通性都不是异步事实，
   * 塞进 state 只会在 effect 里产生一次同步 setState（级联渲染）。
   */
  const degraded: DegradedReason | null =
    !canUseIndex ? 'unsupported' : offline ? 'offline' : loadFailure

  /**
   * 拉取。**落态一律在 `.then` / `.catch` 回调里**——这个函数由 effect 触发，
   * 同步（含 await 之前）落态会触发级联渲染（本仓的 `react(set-state-in-effect)` 门禁）。
   */
  const load = useCallback(() => {
    if (!canUseIndex || offline) return
    const seq = ++loadSeqRef.current
    conversationBridge.listConversations!()
      .then((res) => {
        if (seq !== loadSeqRef.current) return
        setItems(Array.isArray(res?.conversations) ? res.conversations : [])
        setLoadFailure(null)
      })
      .catch((e: unknown) => {
        if (seq !== loadSeqRef.current) return
        // 未登录与断网都走降级，而不是报错——列表本来就有一份本机缓存可读
        setItems([])
        setLoadFailure(codeOf(e) === 'auth_lost' ? 'unauthenticated' : 'offline')
      })
      .finally(() => {
        if (seq === loadSeqRef.current) setLoaded(true)
      })
  }, [canUseIndex, offline, conversationBridge])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  // 降级态下不会去拉取，也就没有「正在加载」可言
  const loading = open && !loaded && degraded === null

  /** 活动桶的**键**：`activeConversationId` 为 null 时它就是本地槽（`__local__`）。
   *  行的 key / id 与它比对——行的 id 经 `rowId` 归一（本地槽在 UI 上是 null）。 */
  const activeKey = activeConversationId ?? LOCAL_CONVERSATION_KEY

  const rows: ConversationRow[] = degraded
    ? degradedRows(activeKey, messages, projections)
    : [
        // 本机未固化的会话**在线时也要列**：它们不在服务端列表里，不列就再也切不回去
        // ——离线轮 / 失败轮留下的历史会变成不可达的死数据（「新建」正是让当前这条
        // 本地会话让位给新会话的动作，见 store 的 `newConversation`）。
        ...localOnlyRows(activeKey, messages, projections),
        ...items.map(c => ({
          id: c.id,
          serverId: c.id,
          // 存量会话升级后服务端 title 为空串（0019 不回溯派生）→ 客户端兜底文案
          title: c.title?.trim() || '未命名会话',
          subtitle: shortTime(c.last_active_at || c.created_at),
          active: c.id === activeKey,
        })),
      ]

  const createConversation = useCallback(() => {
    if (degraded || streaming) return
    setError(null)
    // 「新建」= 开一条新会话（store 的 `newConversation`）：服务端在首轮不带
    // conversation_id 时自建（02f 口径），客户端只需把活动桶换成空的本地槽。
    // 当前这条本地会话（若有内容）**铸一个本地键留在列表里**——既不静默清空，
    // 也不像早先的 `switchConversation(null)` 那样在本地槽上退化成空操作。
    newConversation()
  }, [degraded, streaming, newConversation])

  const selectConversation = useCallback((id: string | null) => {
    if (streaming) return
    // 降级态**允许**切换：切换只读本机缓存，不是写操作（禁用的是新建与重命名 /
    // 删除，见卡面「只读本地投影缓存、禁用新建」）
    switchConversation(id)
  }, [streaming, switchConversation])

  const renameConversation = useCallback(async (id: string, title: string) => {
    const trimmed = title.trim()
    if (!trimmed) return
    if (degraded || streaming) return
    setError(null)
    try {
      const updated = await conversationBridge.renameConversation!(id, trimmed)
      setItems(prev => prev.map(c => (c.id === id
        ? { ...c, title: updated?.title?.trim() || trimmed }
        : c)))
    } catch (e) {
      setError(OP_ERROR_ZH[codeOf(e)] ?? '重命名失败，请稍后重试')
    }
  }, [conversationBridge, degraded, streaming])

  const deleteConversation = useCallback(async (id: string) => {
    if (degraded || streaming) return
    setError(null)
    // 确定性回退目标：列表里**剩下的第一条**（服务端按活跃倒序 → 最近活跃的另一条），
    // 一条不剩则回退到本地会话桶（null）。先算好再删，避免刷新竞态。
    const fallbackId = items.find(c => c.id !== id)?.id ?? null
    try {
      await conversationBridge.deleteConversation!(id)
    } catch (e) {
      // 404 = 「这个 id 不是我的会话了」——与成功同义（routes.py 的重试契约），
      // 照常做本地清理，不弹错
      if (codeOf(e) !== 'not_found') {
        setError(OP_ERROR_ZH[codeOf(e)] ?? '删除失败，请稍后重试')
        return
      }
    }
    removeConversation(id, fallbackId)
    setItems(prev => prev.filter(c => c.id !== id))
  }, [conversationBridge, degraded, streaming, items, removeConversation])

  return {
    rows, loading, degraded, error, busy: streaming, activeConversationId,
    createConversation,
    selectConversation,
    renameConversation,
    deleteConversation,
    dismissError: () => setError(null),
  }
}

/** 降级态的行：**全部**本机缓存（服务端列表拿不到，这里是唯一的读取面）。 */
function degradedRows(
  activeKey: string,
  messages: AssistantMessage[],
  projections: Record<string, AssistantMessage[]>,
): ConversationRow[] {
  const rows = [makeRow(activeKey, messages, activeKey, '')]
  for (const [key, msgs] of Object.entries(projections)) {
    if (key === activeKey) continue
    rows.push(makeRow(key, msgs, activeKey, ''))
  }
  return rows
}

/**
 * 本机**未固化到服务端**的会话：当前那条（有内容才算一条会话）+ 缓存里的本地桶。
 *
 * 服务端会话不在这里出——在线时权威列表在服务端（`items`），离线时走
 * `degradedRows`（服务端列表拿不到，只能读本机缓存）。
 */
function localOnlyRows(
  activeKey: string,
  messages: AssistantMessage[],
  projections: Record<string, AssistantMessage[]>,
): ConversationRow[] {
  const rows: ConversationRow[] = []
  if (isLocalConversationKey(activeKey) && messages.length > 0) {
    rows.push(makeRow(activeKey, messages, activeKey, LOCAL_SUBTITLE))
  }
  for (const [key, msgs] of Object.entries(projections)) {
    if (key === activeKey || msgs.length === 0) continue
    if (!isLocalConversationKey(key)) continue
    rows.push(makeRow(key, msgs, activeKey, LOCAL_SUBTITLE))
  }
  return rows
}

/** 桶键 → 列表行的 id：本地槽（`__local__`）在 UI 上就是 `null`（开它就是
 *  `switchConversation(null)`）。 */
const rowId = (key: string) => (key === LOCAL_CONVERSATION_KEY ? null : key)

/** 一个桶 → 一行。`serverId` 由键空间推得——本地键没有服务端行。 */
function makeRow(
  key: string, msgs: AssistantMessage[], activeKey: string, subtitle: string,
): ConversationRow {
  return {
    id: rowId(key),
    serverId: toServerConversationId(key),
    title: titleFromMessages(msgs),
    subtitle,
    active: key === activeKey,
  }
}

function codeOf(e: unknown): string {
  return e instanceof Error && 'code' in e ? String((e as { code: unknown }).code) : ''
}