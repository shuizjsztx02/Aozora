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
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { AiStreamDoneData, AiStreamEvent } from '../../platform/bridge/bridge-types'

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
   * `confirm` 帧带来的挂起提案。**这是实时信号，不是权威清单**（`stream.py` 口径：
   * `done.proposals` 才是权威，按 `proposalId` 去重即收敛）。本卡只留存不渲染。
   */
  confirmProposal: Record<string, unknown> | null
  /** `device_request` 帧（P1 无生产者——设备能力通道归 P3）；原样留存，不丢帧 */
  deviceRequests: Array<Record<string, unknown>>
  /** `done` 帧载荷（null = 尚未收到，或本轮以失败/取消收尾） */
  done: AgentStreamDone | null
  /** 失败收尾的错误码与文案（null = 无错）。**不覆盖 `text`**——半截文本保留 */
  error: { code: string; message: string } | null
  /** 用户主动取消。与 `error` 是两件事：前者主动、后者被动，UI 表现与恢复路径不同
   *  （03e 的边界），故不合并成一个状态 */
  canceled: boolean
  /** 已收尾——`done` / `error` / 取消**只认先到的那个**（桥侧可能在流正常结束后
   *  补发一个本地 `error`，如连接关闭） */
  settled: boolean
}

export function createStreamProjection(): AgentStreamProjection {
  return {
    text: '', reasoning: '', steps: [], confirmProposal: null,
    deviceRequests: [], done: null, error: null, canceled: false, settled: false,
  }
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
    case 'confirm':
      return { ...p, confirmProposal: (d.proposal as Record<string, unknown>) ?? null }
    case 'device_request':
      return { ...p, deviceRequests: [...p.deviceRequests, d] }
    case 'done':
      return { ...p, settled: true, done: d as unknown as AgentStreamDone }
    case 'error':
      // 保留半截文本（架构 §8.5）：只标注失败，`text` / `reasoning` 原样留在投影里
      return { ...p, settled: true, error: { code: str(d.code) ?? 'unknown', message: str(d.message) ?? '' } }
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
  bridge, onSettled,
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
  /** 收尾回调每次渲染都是新函数（依赖 use-assistant 的卡状态）→ 取最新的那份。
   *  在 effect 里同步（不在渲染期写 ref——渲染期写会在并发渲染下留下被丢弃那次的
   *  值）；收尾只可能由桥事件或用户取消触发，都在 commit 之后。 */
  const onSettledRef = useRef(onSettled)
  useEffect(() => { onSettledRef.current = onSettled })

  const unsubscribe = useCallback(() => {
    unsubRef.current?.()
    unsubRef.current = null
  }, [])

  // 卸载兜底退订（防事件打到已卸载的 hook）
  useEffect(() => unsubscribe, [unsubscribe])

  /** 收尾（幂等的唯一入口）：退订 → 交投影 → 卸载气泡。 */
  const settle = useCallback((final: AgentStreamProjection) => {
    settledRef.current = true
    unsubscribe()
    onSettledRef.current(final)
    projectionRef.current = null
    setProjection(null)
  }, [unsubscribe])

  const start = useCallback(async (
    input: string, bookId: string, conversationId: string | null,
  ) => {
    const agentBridge = bridge as Partial<AgentStreamBridge>
    // 上一轮的兜底退订（正常收尾已退订；这里防「流未收尾就又起一轮」的残流）
    unsubscribe()
    const round = ++roundRef.current
    const initial = createStreamProjection()
    projectionRef.current = initial
    settledRef.current = false
    streamIdRef.current = null
    setProjection(initial)

    const onEvent = (ev: AgentStreamEvent) => {
      if (settledRef.current || roundRef.current !== round) return
      if (streamIdRef.current && ev.streamId !== streamIdRef.current) return
      const next = applyStreamEvent(projectionRef.current ?? initial, ev)
      projectionRef.current = next
      if (next.settled) { settle(next); return }
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
  }, [bridge, settle, unsubscribe])

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