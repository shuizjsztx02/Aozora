/**
 * T-M7-04e：**投影 B 的重建**——服务端事件日志 → 客户端的消息列表。
 *
 * 决策背景见 `docs/adr/ADR-008-conversation-cross-device.md`：会话正文**不做双向同步
 * 实体**，而是**按需从服务端事件日志重建**。理由是投影 B 本身就是**派生数据**——架构
 * §6.2 的事实来源是服务端的事件日志，客户端的 `assistant-chat-store` 只是它的一层缓存。
 * 把派生数据提升成同步实体（接入 sync 模块、定冲突策略）等于给一份不产生新事实的数据
 * 造第二个写入者：凭空多出一套冲突消解，却换不来任何一条本地独有的信息。
 *
 * 于是本模块是**纯函数**：日志进、消息列表出。没有本地写入、没有合并规则、没有冲突
 * 消解——服务端永远是权威，重建只是把它的日志投影成能渲染的形状。
 *
 * **与 `use-assistant-stream.ts` 的 `recoveredMessages` 的分工**：那个管断连时「补几条」，
 * 必须与**正在渲染的半截气泡**比对去重（它拿得到 `rendered`）；本函数管一整个会话的
 * 历史，从零建一张列表，没有可以比对的对象。两者都是「事件 → 正文」，但一个是有状态
 * 的补救、一个是无状态的投影，刻意不合并。
 *
 * **重建得到什么、得不到什么：** 只有用户与助手的**正文**，外加失败轮的**中断标注**
 * （`error` 事件，与实时路径用同一份文案 `INTERRUPTED_TURN_NOTE`）。得不到的是
 * **历史只读记账卡**——提案的日志留档是 `approval_request` / `approval_result` 事件，
 * 而它们当前**没有生产者**（04a 把这个生产者记在「谁要做『从日志重建确认卡』谁接」
 * 名下，未指派）。因此调用方的规则是「**本地已有投影就不重建**」（见
 * `use-conversations.ts`），本机看过的卡片不会因为一次刷新而消失。
 *
 * 步骤卡片（03c）不在这里的损失项里：`AssistantMessage` 里本来就没有它，同一条会话在
 * 本机重新载入（localStorage 里出来的那份）同样没有——重建与原生的保真度是一致的。
 */

import type { AiConversationEventItem } from '../../platform/bridge/bridge-types'
import type { AssistantMessage } from './use-assistant'

/**
 * 失败轮在历史里的落点（T-M7-04b 起实时路径就写这一句）。
 *
 * 重建侧复用同一个常量是为了**同一件事只有一份文案**：同一条会话在发起设备与本设备
 * 上必须显示得一样。实时路径还会追加「；未确认的改动没有记录」——那句来自本机的挂起
 * 提案状态，日志里没有，重建拿不到，也不该编。
 */
export const INTERRUPTED_TURN_NOTE = '⚠️ 连接中断，本轮未完成'

/** 事件 `seq` → 消息 id。`seq` 在会话内唯一，重建因此**幂等**——同一批事件重放两次
 *  得到同一串 id，不会因为重复重建而长出两份消息。 */
const messageId = (seq: number) => `evt_${seq}`

/**
 * 投影 B 的消息来源：**只有这两类 Surface 事件是「一条消息」**。
 *
 * `tool` 事件刻意不在其中——它是助手那条消息的内部细节（工具结果），渲染上没有独立的
 * 位置；结构性事件（`quota` / `replace` / `approval_*` / `device_request` / `security`）
 * 是留档，不产消息，只有 `error` 例外（见下）。
 */
const MESSAGE_EVENT_TYPES = new Set(['user', 'assistant'])

/** 事件载荷里的正文（`assistant` 一定带 `content` 键；畸形/缺字段一律空串）。 */
function contentOf(data: Record<string, unknown>): string {
  return typeof data.content === 'string' ? data.content : ''
}

/**
 * 整段日志 → 消息列表（按 `seq` 升序，与渲染顺序一致）。
 *
 * 三条取舍：
 *
 * ① **空正文的 `assistant` 事件不产消息**。带 `tool_calls` 的中间步骤正文就是空的，
 *    它们要渲染成步骤卡片——而卡片是**瞬时 UI**（本卡不做重建），产出空气泡只是噪音。
 * ② **`error` 事件产一条中断标注**而不是被丢掉：丢掉的话，被中断的那一轮在重建出来的
 *    历史里会凭空消失，用户看到的是「我说了话，助手没理我」。这是与实时路径对齐的那一条。
 * ③ **不做任何去重**。同一个 `seq` 只会有一条事件，而 `after_seq` 是严格大于——重复
 *    只可能来自调用方把两批重叠的事件拼在一起，那是调用方的错，不该在这里用正文去猜
 *    （正文去重会误伤「午饭 12.5」这种真重复的轮次）。
 */
export function projectHistoryFromEvents(
  events: AiConversationEventItem[],
): AssistantMessage[] {
  const out: AssistantMessage[] = []
  for (const event of [...events].sort((a, b) => a.seq - b.seq)) {
    if (event.type === 'error') {
      out.push({ id: messageId(event.seq), role: 'assistant', text: INTERRUPTED_TURN_NOTE })
      continue
    }
    if (!MESSAGE_EVENT_TYPES.has(event.type)) continue
    const text = contentOf(event.data)
    if (!text.trim()) continue
    out.push({
      id: messageId(event.seq),
      role: event.type === 'user' ? 'user' : 'assistant',
      text,
    })
  }
  // R-PROD-33：尾部的**孤立 user** 补一行中断标注。
  //
  // 流式中杀进程 / 断连的形态在服务端日志里就是「只剩一条 `user` 事件」——既没有
  // 答案，也没有 `error` 事件（服务端的工作协程随断连取消，来不及写任何收尾）。
  // 不补的话，重建出来的历史里那是一条「没有回答的问题」，用户会以为它被忽略了，
  // 而不是「当时断了」；实时路径当场就会写同一句话，两边因此不一致。
  //
  // id 由那条 user 的消息 id 派生（不是新 `seq`），重建因此仍然**幂等**——同一批
  // 事件重放两次不会长出两条标注。
  const last = out[out.length - 1]
  if (last?.role === 'user') {
    out.push({ id: `${last.id}_interrupted`, role: 'assistant', text: INTERRUPTED_TURN_NOTE })
  }
  return out
}

/** 一批事件里的最大 `seq`（空批 → 0）。它是「这一批读到哪儿了」的游标。 */
export function maxSeqOf(events: AiConversationEventItem[]): number {
  return events.reduce((max, e) => (e.seq > max ? e.seq : max), 0)
}