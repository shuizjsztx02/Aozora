/**
 * T-M7-03b（P2）：agent 流事件的**投影**与**收发接线**——从 `use-assistant.ts` 拆出。
 *
 * 拆分理由（本卡卡面）：T-M7-02i 把流式消费内联在 1300 行的 `use-assistant.ts` 里
 * （类型 + `runAgentTurn` + `settleStream`），与卡流程、NLU 分发矩阵混在一处。P2 要在
 * 其上叠步骤卡片流（03c）、多会话（03d）与取消/打断（03e），先把它切成两块：
 *
 *   - **纯 reducer**（`applyStreamEvent`）：事件 → 投影态。无 React、无副作用，
 *     八类事件逐类可断言（`__tests__/features/assistant-stream-projection.test.ts`）。
 *   - **hook**（`useAssistantStream`）：订阅 / 退订、`streamId` 归属、收尾幂等、取消。
 *     它**不碰任何业务状态**——收尾时把最终投影整份交给上层，由 `use-assistant` 按
 *     既有产物分发矩阵落点（回执 / 挂起卡 / 文本气泡）。这样「投影怎么算」与
 *     「产物落到哪张卡」各自独立演化。
 *
 * 投影放什么：渲染需要的一切（正文与推理增量、步骤、挂起提案、设备指令、结果与错误）。
 * 服务端载荷字段表以 `server/src/modules/ai/stream.py` 为准，**本文件不新增字段语义**。
 *
 * 三条来自 02g-2 / 02i 的硬约束在本文件落地：
 *   ① `delta` 按 `channel` 分流，reasoning 绝不混进正文（架构 §8.5）；
 *   ② `error` 帧**保留已渲染的半截文本、不清空**（架构 §8.5）——reducer 只标注失败，
 *      文本留在投影里，由上层定型成消息；
 *   ③ **不假设「每条回复都从 `delta` 开始」**——快档直调工具那一步 0 帧 `delta`
 *      （02g-2 探针），「有反应」靠 `tool_call` / `tool_result` 的步骤可见性。
 *
 * T-M7-03e：`confirm` 帧由「只留最近一条」改为一**队**（按 `proposalId` 去重累积），
 * 并新增 `onConfirm` 在帧到达时**实时转发**给上层——确认卡因此能事件驱动地出现，
 * 而不是等 `done` 之后按产物分发（P1 的时序）。本 hook 仍不碰业务状态：转发的是
 * 「又有了一张挂起提案」这件事本身；可不可执行由 `done.proposals`（权威清单）决定。
 *
 * T-M7-04b：**断连重连续跑**的客户端一半。服务端自本卡起**逐事件落库**（每个配对组
 * 一写一提交），所以断连时这一轮**已经跑完的步骤留在日志里**；本 hook 在失败收尾前
 * 按游标（上一轮 `done.seq`）调 `conversation.events?after_seq=` 续读一次，把补得到
 * 的正文放进投影的 `recovered` 交给上层（见 `recoveredMessages` 的三条裁剪规则）。
 * **游标是内存态**、续读失败退化成「什么都不补」——补救不该把中断提示本身拖没。
 *
 * T-M7-04e：投影多一个**交接字段** `logCursor`（收尾时本机已并入日志的到哪儿）。
 * 本 hook 的行为一行未变——它只是把 `settle` 那一刻的游标一并交出去，由上层落盘，
 * 供 `use-conversations` 的跨设备增量补齐当起点（决策见
 * `docs/adr/ADR-008-conversation-cross-device.md`）。
 *
 * T-M7-05f：新增 `onDeviceRequest`——`device_request` 帧到达时**实时**转发（与
 * `onConfirm` 同范式），由上层用 `device-executor` 落地。本 hook 仍不碰业务状态，
 * 也不认识任何能力名。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  AiApprovalResultAck,
  AiApprovalResultParams,
  AiConversationEventItem,
  AiConversationEventsResponse,
  AiStreamDoneData,
  AiStreamEvent,
  DeviceRequestFrame,
} from '../../platform/bridge/bridge-types'

/**
 * T-M7-02i：agent 流式通道的客户端桥契约（**实现见 T-M7-02h**）。
 *
 * 结构化窄接口 + 运行时能力探测：它表达的是「桥**可能**不具备流式能力」（未落地的
 * 桥、旧 APK 的命令表里没有 `ai.agent` / `ai.cancel`），与 `bridge-types.ts` 里的
 * **契约类型**不是一回事——后者是载荷形状的唯一源，本文件只做别名。
 */
export interface AgentStreamBridge {
  runAgent(
    input: string, bookId: string, conversationId?: string | null,
  ): Promise<{ pending: boolean; streamId: string }>
  onAiStream(listener: (event: AgentStreamEvent) => void): () => void
  /** 关掉 SSE 长连接——服务端把断连当取消（没有单独的取消端点）。
   *  可选：能力探测不要求它（取消在桥缺席时退化为「只收敛本地态」）。 */
  cancelAgent?(): Promise<{ cancelled: boolean }>
  /** T-M7-04b：按 `after_seq` 续读会话事件（断连重连的续读入口）。
   *  可选：能力探测不要求它——桥缺席时续跑退化为「什么都不补」，中断提示照旧。 */
  fetchConversationEvents?(
    id: string, afterSeq?: number | null,
  ): Promise<AiConversationEventsResponse>
  /** M8-02b：审批四值上报（审计留档）。
   *  可选：能力探测不要求它——旧桥缺席时只少了审计，四值的 UI 行为不变。 */
  reportApprovalResult?(
    params: AiApprovalResultParams,
  ): Promise<AiApprovalResultAck>
}

/** agent 流的下行事件——**契约源在 `bridge-types.ts`**（T-M7-02h 已提交）。 */
export type AgentStreamEvent = AiStreamEvent

/** `done` 帧的载荷 —— 同上，契约源在 `bridge-types.ts`。 */
export type AgentStreamDone = AiStreamDoneData

/**
 * T-M7-02i：流式期间的内存态气泡（**不落 messages store**）。
 *
 *  瞬时态不落盘与 T14-4 决策 #7 同口径（processing / pendingOp 等本就不进 store）；
 *  两个实际理由：① 一轮几十上百帧增量，逐帧写 localStorage 的序列化开销与帧数同阶；
 *  ② 架构 §8.5 明确「本轮不落库，重新打开会话时这半截文本不在历史里」——落盘会让
 *  用户重开 App 看到半截文本且没有失败标注。done / error 时定型成正式消息。
 */
export interface StreamingBubble {
  /** 正文增量拼接（`delta.data.channel === 'content'`） */
  text: string
  /** 推理增量拼接（`delta.data.channel === 'reasoning'`；渲染为灰字折叠区，
   *  绝不混进正文——架构 §8.5） */
  reasoning: string
}

/**
 * 一个 Step 的投影（`phase` / `tool_call` / `tool_result` 按 `step` 归并）。
 *
 * 三态即 03c 步骤卡片的「进行中 / 完成 / 失败」：`status === null` 且已有 `phase`
 * 或 `tool` → 进行中；`ok` → 完成；`error` → 失败。本卡只建投影，不渲染。
 */
export interface AgentStepView {
  /** 1 起的 Step 序号（服务端载荷的 `step`） */
  step: number
  /** 阶段标识（thinking / routing / tool_calling），取最近一次 `phase` 帧 */
  phase: string | null
  /** 本步的推理档位（`phase.reasoning`：fast / medium / deep；仅供显示「深度思考」） */
  reasoning: string | null
  /** 工具名（`tool_call` / `tool_result` 的 `tool`） */
  tool: string | null
  /** 工具的人类可读文案（`tool_call.message`：「正在查询预算…」） */
  message: string | null
  /** 工具结果：null = 未结束（进行中）/ `ok` 完成 / `error` 失败 */
  status: 'ok' | 'error' | null
  /** 失败的安全枚举码（仅 `status === 'error'`） */
  code: string | null
}

/** 事件 → 状态的投影态。渲染读它，落点判据也读它（`done` / `error` / `canceled`）。 */
export interface AgentStreamProjection {
  /** 正文增量拼接（`channel === 'content'`） */
  text: string
  /** 推理增量拼接（`channel === 'reasoning'`） */
  reasoning: string
  /** 步骤投影，按到达顺序（同 `step` 的多类帧归并进同一条） */
  steps: AgentStepView[]
  /**
   * `confirm` 帧带来的挂起提案，按到达顺序累积（同一 `proposalId` 只留第一条）。
   * **这是实时信号，不是权威清单**（`stream.py` 口径：`done.proposals` 才是权威）。
   * T-M7-03e 起由 `use-assistant` 消费它**事件驱动**地建确认卡（03b 时只留存不渲染）；
   * 收尾时仍以 `done.proposals` 核对——不在权威清单里的提案不可被确认。
   */
  confirmProposals: Array<{
    step: number | null
    proposal: Record<string, unknown>
    /** T20-4（REQ-78）：服务端**为什么**要问的理由码（`confirmReason`，
     *  `high_risk` / `medium_risk` / `multi_step` / `low_confidence`）。
     *  `null` = 帧里没带（设备 op，或「没人可问」那一档）——卡片那时不显示理由。
     *  **不认识的值也不显示**：理由码是服务端的判定，客户端只做码→文案的映射。 */
    reason: string | null
  }>
  /** `device_request` 帧（T-M7-05e 起有生产者，ADR-010）；原样留存，不丢帧。
   *  **执行不在这里**——帧到达即由 `onDeviceRequest` 转发给上层（`device-executor`），
   *  本投影只让步骤卡片看得见「助手在做一次设备操作」。 */
  deviceRequests: Array<Record<string, unknown>>
  /** `done` 帧载荷（null = 尚未收到，或本轮以失败/取消收尾） */
  done: AgentStreamDone | null
  /** 失败收尾的错误码与文案（null = 无错）。**不覆盖 `text`**——半截文本保留 */
  error: { code: string; message: string; conversationId: string | null } | null
  /** 用户主动取消。与 `error` 是两件事：前者主动、后者被动，UI 表现与恢复路径不同
   *  （03e 的边界），故不合并成一个状态 */
  canceled: boolean
  /** 已收尾——`done` / `error` / 取消**只认先到的那个**（桥侧可能在流正常结束后
   *  补发一个本地 `error`，如连接关闭） */
  settled: boolean
  /**
   * 断连收尾时从服务端日志**续读**回来的补记（T-M7-04b，按 `seq` 升序）。
   *
   * 空数组 = 没有可补的（没断连 / 桥不支持 / 续读失败 / 服务端也没留下什么）。
   * 只承载**可渲染的助手正文**——工具步骤与结构性事件是投影 B 的内部细节，重建
   * 它们不在本卡（见 `recoveredMessages`）。
   */
  recovered: RecoveredMessage[]
  /**
   * T-M7-04e：这一轮收尾时**本机已并入日志的到哪儿**（事件 `seq`；0 = 还没并过）。
   *
   * 与 `recovered` 同样是**交接字段**而非渲染态：上层（`use-assistant`）在收尾时
   * 把它写进 store 的 `cursors`，作为跨设备增量补齐的起点。
   *
   * 值取 `settle` 那一刻的游标，因此两条路径都对：正常收尾是 `done.seq`；**失败
   * 收尾是续读拉到的最大 `seq`**（`catchUp` 先跑、已经推进了游标）——后者是必须的，
   * 否则失败轮留在日志里的 `user` 事件会在下一次增量补齐时**重复显示一次**。
   */
  logCursor: number
}

/** 续读回来的一条补记：`seq` 是它在日志里的位置，`text` 是要补的正文。 */
export interface RecoveredMessage {
  seq: number
  text: string
}

export function createStreamProjection(): AgentStreamProjection {
  return {
    text: '', reasoning: '', steps: [], confirmProposals: [],
    deviceRequests: [], done: null, error: null, canceled: false, settled: false,
    recovered: [], logCursor: 0,
  }
}

/**
 * T-M7-04b：续读回来的事件 → 要补的正文。
 *
 * 三条规则，每条都对应一个真实约束：
 *
 * ① **只取最后一批里的最后一轮**。断连只可能打断**正在跑的那一轮**，而 `after_seq`
 *    起点不保证精确（首轮没有游标时会拉回整个日志）——按 `turn` 取最大的那一轮，
 *    前面的轮次客户端本地早就有，重复追加就是重影。
 * ② **只取 `assistant` 的正文**。`user` 事件一定是本端自己写下的（P1/P2 的端点不
 *    接收中途输入），工具事件与结构性事件是投影 B 的内部细节（步骤卡片是瞬时 UI）。
 * ③ **已在半截气泡里的不再追加**。本地 `text` 是**所有步骤** `delta` 的拼接，因此
 *    跑完那几步的正文本来就在里面；真正补得到的只有「服务端其实写完了这一轮、
 *    但 `done` 帧没送到客户端」那一类——那时日志里的正文比半截气泡长。
 */
export function recoveredMessages(
  events: AiConversationEventItem[], rendered: string,
): RecoveredMessage[] {
  if (events.length === 0) return []
  const lastTurn = Math.max(...events.map(e => e.turn))
  const out: RecoveredMessage[] = []
  for (const event of events) {
    if (event.turn !== lastTurn || event.type !== 'assistant') continue
    const text = typeof event.data.content === 'string' ? event.data.content : ''
    if (!text.trim() || rendered.includes(text)) continue
    out.push({ seq: event.seq, text })
  }
  return out
}

/** 提案的服务端 id（去重键）。不是字符串（畸形帧）→ null，该帧无法去重。 */
function proposalIdOf(p: Record<string, unknown>): string | null {
  return typeof p.proposalId === 'string' ? p.proposalId : null
}

const emptyStep = (step: number): AgentStepView => ({
  step, phase: null, reasoning: null, tool: null, message: null, status: null, code: null,
})

/** 按 `step` 归并（同一步会来 `phase` + `tool_call` + `tool_result` 多帧）。 */
function withStep(
  steps: AgentStepView[], step: number, patch: Partial<AgentStepView>,
): AgentStepView[] {
  const i = steps.findIndex(s => s.step === step)
  if (i < 0) return [...steps, { ...emptyStep(step), ...patch }]
  const next = steps.slice()
  next[i] = { ...next[i], ...patch }
  return next
}

/** 载荷里的 `step`；不是数字（畸形帧）→ null，该帧整条丢弃。 */
function stepOf(d: Record<string, unknown>): number | null {
  return typeof d.step === 'number' ? d.step : null
}

const str = (v: unknown): string | null => (typeof v === 'string' ? v : null)

/**
 * 八类事件 → 投影态。**纯函数**（不改入参、无副作用），可单独测。
 *
 * 已 `settled` 的投影对后续帧一律返回原值——收尾幂等是桥侧行为的现实要求：
 * `done` 之后补发的本地 `error`（连接关闭）不能再改投影。
 */
export function applyStreamEvent(
  p: AgentStreamProjection, ev: AgentStreamEvent,
): AgentStreamProjection {
  if (p.settled) return p
  const d = (ev.data ?? {}) as Record<string, unknown>

  switch (ev.event) {
    case 'delta': {
      // 非字符串增量（畸形帧）丢弃：拼进去会得到 "undefined" 这样的正文
      if (typeof d.text !== 'string') return p
      return d.channel === 'reasoning'
        ? { ...p, reasoning: p.reasoning + d.text }
        : { ...p, text: p.text + d.text }
    }
    case 'phase': {
      const step = stepOf(d)
      if (step === null) return p
      const patch: Partial<AgentStepView> = { phase: str(d.phase) }
      // 档位只挂在**步骤起点**那一帧上（`step_started`）；工具开始那帧
      // （`tool_started` → phase=tool_calling）不带 `reasoning`——缺省时不能把它
      // 清掉，否则「深度思考」的档位会在工具开始的那一刻丢失
      if (typeof d.reasoning === 'string') patch.reasoning = d.reasoning
      return { ...p, steps: withStep(p.steps, step, patch) }
    }
    case 'tool_call': {
      const step = stepOf(d)
      if (step === null) return p
      return { ...p, steps: withStep(p.steps, step, {
        tool: str(d.tool), message: str(d.message),
      }) }
    }
    case 'tool_result': {
      const step = stepOf(d)
      if (step === null) return p
      return { ...p, steps: withStep(p.steps, step, {
        tool: str(d.tool),
        status: d.status === 'error' ? 'error' : 'ok',
        code: str(d.code),
      }) }
    }
    case 'confirm': {
      const proposal = d.proposal as Record<string, unknown> | undefined
      if (!proposal) return p
      // 同一提案重复到达（重发帧 / 畸形流）不重复成卡——`proposalId` 是服务端 id
      const id = proposalIdOf(proposal)
      if (id && p.confirmProposals.some(c => proposalIdOf(c.proposal) === id)) return p
      return { ...p, confirmProposals: [...p.confirmProposals, {
        step: stepOf(d), proposal, reason: str(d.confirmReason),
      }] }
    }
    case 'device_request':
      return { ...p, deviceRequests: [...p.deviceRequests, d] }
    case 'done':
      return { ...p, settled: true, done: d as unknown as AgentStreamDone }
    case 'error':
      // 保留半截文本（架构 §8.5）：只标注失败，`text` / `reasoning` 原样留在投影里。
      // `conversation_id` 一并留下（T-M7-04b）：首轮还没有会话 id 时，它是续读的
      // 唯一线索（服务端在失败帧里回传，会话索引在前置检查里已经提交）。
      return { ...p, settled: true, error: {
        code: str(d.code) ?? 'unknown',
        message: str(d.message) ?? '',
        conversationId: str(d.conversation_id),
      } }
    default:
      return p
  }
}

export interface UseAssistantStreamOptions {
  /** 桥（`useBridge()` 的产物）；能力探测与闸门在 `use-assistant` 侧，这里只管收发 */
  bridge: unknown
  /** 收尾回调：`done` / `error` / 取消三种收尾各调用一次，载荷是**最终投影**。
   *  实现方（use-assistant）负责落点与三态收敛。 */
  onSettled: (projection: AgentStreamProjection) => void
  /** T-M7-03e：`confirm` 帧到达时**实时**转发一次（事件驱动建卡）。
   *  本 hook 仍不碰业务状态——只把「又有了一张挂起提案」这件事告诉上层。
   *  T20-4（REQ-78）：`reason` 是同一帧带下来的**理由码**（null = 没带），
   *  上层把它记在卡上——本 hook 同样不解释它（码→文案的映射在卡片里）。 */
  onConfirm?: (
    proposal: Record<string, unknown>, step: number | null, reason: string | null,
  ) => void
  /**
   * T-M7-05f：`device_request` 帧到达时**实时**转发一次（事件驱动执行设备动作）。
   *
   * 与 `onConfirm` 同范式：本 hook 不碰业务状态，只把「又来了一条设备指令」交出去，
   * 由上层（`use-assistant`）用 `device-executor` 落地。**必须实时**——需结果类在
   * 服务端是挂起等回传的，攒到收尾再执行就等于让 loop 白等一个超时窗。
   */
  onDeviceRequest?: (frame: DeviceRequestFrame, step: number | null) => void
}

export interface AssistantStreamHandle {
  /** 当前投影（null = 不在流式中）。气泡与步骤卡片都读它 */
  projection: AgentStreamProjection | null
  /** 起一轮——**闸门（开关 + 桥能力探测）在 `use-assistant` 侧，本函数不做判断** */
  start: (input: string, bookId: string, conversationId: string | null) => Promise<void>
  /** 用户主动取消：关掉 SSE 长连接并把本轮按「已取消」收尾。**不做任何写** */
  cancel: () => Promise<void>
}

/**
 * T-M7-03b：agent 流一轮的收发。
 *
 * 与旧内联实现的差别：① 事件不再攒在 ref 里手工拼字符串，交给纯 reducer；
 * ② 退订与「只收尾一次」的幂等收敛到本 hook，`use-assistant` 只面对一个回调；
 * ③ 取消有了正经入口（P1 只留了桥协议，没有调用点）。
 */
export function useAssistantStream({
  bridge, onSettled, onConfirm, onDeviceRequest,
}: UseAssistantStreamOptions): AssistantStreamHandle {
  const [projection, setProjection] = useState<AgentStreamProjection | null>(null)
  /** 同步镜像：事件处理器是 `start` 时建的闭包，靠 setState 的 prev 拿不到
   *  「刚到的这一帧是否收尾了」——收尾要当场决定退订与回调。 */
  const projectionRef = useRef<AgentStreamProjection | null>(null)
  /** 本轮是否已收尾。初值 true = 没有活动流（取消时据此判断要不要收尾） */
  const settledRef = useRef(true)
  /** 本轮流 id（`runAgent` 的 ack 里给）；ack 到达前的事件按单流处理
   *  （`submitInput` 的并发互斥保证同时只有一条流） */
  const streamIdRef = useRef<string | null>(null)
  /** 本轮序号（`start` 每次自增）。让**迟到的失败认得出自己还是不是当前轮**——
   *  否则「流未收尾就又起一轮」时，上一轮迟到的桥层 reject 会把**新一轮**收尾掉。
   *  `submitInput` 的并发互斥之外的第二道防线（那一层在别的模块里，不该被本模块依赖）。 */
  const roundRef = useRef(0)
  const unsubRef = useRef<(() => void) | null>(null)
  /** T-M7-04b：本会话的**续读游标**——`{会话 id, 已读到的事件序号}`。
   *
   *  内存态（不落盘）：断连续读是**同一次会话交互内**的补救，`after_seq` 起点不精确
   *  只影响取多少行、不影响正确性（`recoveredMessages` 按轮裁剪，见那里的规则 ①）。
   *  换会话（调用方给的 id 变了）即重置。
   */
  const cursorRef = useRef<{ conversationId: string | null; seq: number }>({
    conversationId: null, seq: 0,
  })
  /** 收尾回调每次渲染都是新函数（依赖 use-assistant 的卡状态）→ 取最新的那份。
   *  在 effect 里同步（不在渲染期写 ref——渲染期写会在并发渲染下留下被丢弃那次的
   *  值）；收尾只可能由桥事件或用户取消触发，都在 commit 之后。 */
  const onSettledRef = useRef(onSettled)
  useEffect(() => { onSettledRef.current = onSettled })
  // 同上：`confirm` 的实时转发也取最新的那份
  const onConfirmRef = useRef(onConfirm)
  useEffect(() => { onConfirmRef.current = onConfirm })
  // T-M7-05f：设备指令的实时转发同此
  const onDeviceRequestRef = useRef(onDeviceRequest)
  useEffect(() => { onDeviceRequestRef.current = onDeviceRequest })

  const unsubscribe = useCallback(() => {
    unsubRef.current?.()
    unsubRef.current = null
  }, [])

  // 卸载兜底退订（防事件打到已卸载的 hook）
  useEffect(() => unsubscribe, [unsubscribe])

  /** 收尾（幂等的唯一入口）：推进游标 → 退订 → 交投影 → 卸载气泡。
   *
   *  **游标**（T-M7-04b）在收尾时推进：`done` 带回服务端会话 id 与 `seq`（本轮结束
   *  时的事件序号），它就是下一次断连时 `after_seq` 的起点。放在 `settle` 而不是
   *  `done` 分支上，是因为取消 / 失败路径也不能把上一轮的游标弄丢。
   *
   *  **T-M7-04e**：同一个值随投影一并交出（`logCursor`）——上层据此在 store 里落盘，
   *  跨设备的下一次增量补齐从它开始。失败收尾走的是 `settleAfterRecovery`，续读已经
   *  把游标推到了服务端日志的真实位置，落盘的因此也是一个精确值。 */
  const settle = useCallback((final: AgentStreamProjection) => {
    if (final.done) {
      cursorRef.current = {
        conversationId: final.done.conversation_id,
        seq: typeof final.done.seq === 'number' ? final.done.seq : cursorRef.current.seq,
      }
    }
    settledRef.current = true
    unsubscribe()
    onSettledRef.current({ ...final, logCursor: cursorRef.current.seq })
    projectionRef.current = null
    setProjection(null)
  }, [unsubscribe])

  /**
   * T-M7-04b：按游标**续读**服务端事件日志，返回要补的正文。
   *
   * 游标来自上一轮 `done.seq`；没有游标（首轮 / 老服务端 / 桥不支持）= 从头拉，
   * 由 :func:`recoveredMessages` 按轮裁剪——结果一样，只是多取一些行。
   *
   * **任何失败都退化成「什么都不补」**：续读是补救，不该把中断提示本身也拖没
   * （网络刚断过，这一次请求再失败是完全正常的）。
   */
  const catchUp = useCallback(async (
    p: AgentStreamProjection,
  ): Promise<RecoveredMessage[]> => {
    const agentBridge = bridge as Partial<AgentStreamBridge>
    const conversationId = cursorRef.current.conversationId ?? p.error?.conversationId ?? null
    if (!conversationId || !agentBridge.fetchConversationEvents) return []
    let events: AiConversationEventItem[]
    try {
      const response = await agentBridge.fetchConversationEvents(
        conversationId, cursorRef.current.seq,
      )
      events = response.events
    } catch {
      return []
    }
    if (events.length > 0) {
      // 游标跟着往前推：同一次断连被连续收尾两次（桥侧补发的 error）时不会重复拉。
      cursorRef.current = {
        conversationId,
        seq: Math.max(...events.map(e => e.seq)),
      }
    }
    return recoveredMessages(events, p.text)
  }, [bridge])

  /** 失败收尾：**先续读、再收尾**。续读是异步的，故这里另立一条路径——
   *  `done` / 取消两条路径仍是同步的、当场收尾，不受影响。 */
  const settleAfterRecovery = useCallback(async (
    p: AgentStreamProjection, round: number,
  ) => {
    const recovered = await catchUp(p)
    // 续读期间可能已经起了新一轮（`round` 变了）或用户按了停止（已收尾）——
    // 那时这一轮的收尾已经与当前界面无关，交给那条路径负责。
    if (roundRef.current !== round || settledRef.current) return
    settle({ ...p, recovered })
  }, [catchUp, settle])

  const start = useCallback(async (
    input: string, bookId: string, conversationId: string | null,
  ) => {
    const agentBridge = bridge as Partial<AgentStreamBridge>
    // 上一轮的兜底退订（正常收尾已退订；这里防「流未收尾就又起一轮」的残流）
    unsubscribe()
    // 换了会话（含「从服务端会话切回本地会话」）→ 游标归零：`after_seq` 是**会话内**
    // 的序号，跨会话复用会取到别人的尾巴（或者什么都取不到）。
    if (conversationId !== cursorRef.current.conversationId) {
      cursorRef.current = { conversationId, seq: 0 }
    }
    const round = ++roundRef.current
    const initial = createStreamProjection()
    projectionRef.current = initial
    settledRef.current = false
    streamIdRef.current = null
    setProjection(initial)

    const onEvent = (ev: AgentStreamEvent) => {
      if (settledRef.current || roundRef.current !== round) return
      if (streamIdRef.current && ev.streamId !== streamIdRef.current) return
      const before = projectionRef.current ?? initial
      const next = applyStreamEvent(before, ev)
      projectionRef.current = next
      if (next.settled) {
        // T-M7-04b：**失败**收尾先续读服务端日志再收尾（断连期间落库的部分要补回来）；
        // `done` / 取消两条路径当场收尾，行为与 03b 逐字相同。
        if (next.error) { void settleAfterRecovery(next, round); return }
        settle(next)
        return
      }
      // T-M7-03e：事件驱动的确认卡——`confirm` 帧当场转发（新累积的那一条）
      if (next.confirmProposals.length > before.confirmProposals.length) {
        const added = next.confirmProposals[next.confirmProposals.length - 1]
        onConfirmRef.current?.(added.proposal, added.step, added.reason)
      }
      // T-M7-05f：事件驱动的设备执行——`device_request` 帧当场转发（新到达的那一条）
      if (next.deviceRequests.length > before.deviceRequests.length) {
        const added = next.deviceRequests[next.deviceRequests.length - 1]
        onDeviceRequestRef.current?.(
          added as unknown as DeviceRequestFrame, stepOf(added),
        )
      }
      setProjection(next)
    }

    unsubRef.current = agentBridge.onAiStream!(onEvent)
    try {
      const ack = await agentBridge.runAgent!(input, bookId, conversationId)
      streamIdRef.current = ack.streamId
    } catch {
      // 桥层失败（旧 APK 无该命令 / 链路建不起来）：同样按本轮失败收尾。
      // **不静默回落单发**——用户看得见失败，而不是被悄悄换一条链路。
      // `roundRef` 比对：这一轮已经被新一轮顶替时，本次失败不该收尾别人。
      if (roundRef.current === round && !settledRef.current) {
        settle({ ...(projectionRef.current ?? initial), settled: true })
      }
    }
    // 命令是「立即返回 ack + 事件推送」的形态（架构 §8.2），所以这里**不退订**：
    // 本轮的结束以 done / error / 取消为准，那三条路径各自负责收尾。
  }, [bridge, settle, settleAfterRecovery, unsubscribe])

  const cancel = useCallback(async () => {
    const agentBridge = bridge as Partial<AgentStreamBridge>
    try {
      // 服务端把断连当取消；无活动流时桥回 {cancelled:false}。桥层取消失败不阻断
      // 本地收敛——用户按了停止，界面必须停下来。
      await agentBridge.cancelAgent?.()
    } catch { /* 同上 */ }
    if (settledRef.current) return
    settle({ ...(projectionRef.current ?? createStreamProjection()), settled: true, canceled: true })
  }, [bridge, settle])

  return { projection, start, cancel }
}