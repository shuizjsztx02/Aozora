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
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { AiConversationListResponse, AiConversationSummary } from '../../platform/bridge/bridge-types'
import { useBridge } from '../../platform/hooks/use-bridge'
import { useAssistantChatStore, LOCAL_CONVERSATION_KEY } from '../../platform/store/assistant-chat-store'
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

/** 列表的一行（已归一化：服务端会话与本机缓存都渲染成它）。 */
export interface ConversationRow {
  /** 服务端会话 id；`null` = 本地会话桶（尚未拿到 id 或全程离线的那条） */
  id: string | null
  title: string
  subtitle: string
  active: boolean
}

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
  /** 当前活动会话 id（null = 本地会话桶）。消息区据此区分「新会话」与
   *  「这条服务端会话的消息不在本机」（正文跨设备同步归 P3 的 04e） */
  activeConversationId: string | null
  createConversation: () => void
  selectConversation: (id: string | null) => void
  renameConversation: (id: string, title: string) => Promise<void>
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

  const rows: ConversationRow[] = degraded
    ? localRows(activeConversationId, messages, projections)
    : items.map(c => ({
        id: c.id,
        // 存量会话升级后服务端 title 为空串（0019 不回溯派生）→ 客户端兜底文案
        title: c.title?.trim() || '未命名会话',
        subtitle: shortTime(c.last_active_at || c.created_at),
        active: c.id === activeConversationId,
      }))

  const createConversation = useCallback(() => {
    if (degraded || streaming) return
    setError(null)
    // 「新建」= 切回本地会话桶：服务端会在首轮不带 conversation_id 时建会话
    // （02f 口径）。若非破坏性优先——本地桶里若有内容（离线轮 / 失败轮留下的一屏），
    // 用户看到的是它，而不是被静默清空。
    switchConversation(null)
  }, [degraded, streaming, switchConversation])

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

/** 降级态的行：本机投影缓存的那些桶（活动桶 + `projections` 的条目）。 */
function localRows(
  activeId: string | null,
  messages: AssistantMessage[],
  projections: Record<string, AssistantMessage[]>,
): ConversationRow[] {
  const rows: ConversationRow[] = []
  // 活动桶在最前（它就是用户此刻看的那条）
  rows.push({
    id: activeId,
    title: titleFromMessages(messages),
    subtitle: '',
    active: true,
  })
  for (const [id, msgs] of Object.entries(projections)) {
    if (id === activeId) continue
    rows.push({
      id: id === LOCAL_CONVERSATION_KEY ? null : id,
      title: titleFromMessages(msgs),
      subtitle: '',
      active: false,
    })
  }
  return rows
}

function codeOf(e: unknown): string {
  return e instanceof Error && 'code' in e ? String((e as { code: unknown }).code) : ''
}