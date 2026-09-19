/**
 * T-M7-03c（P2）：agent 步骤卡片流——把投影里的 `phase` / `tool_call` /
 * `tool_result` 渲染成**可折叠的步骤列表**（架构 §9.1 统一智能模式、§9.2 交互对照）。
 *
 * 三条设计口径（卡面 + 架构）：
 *
 *   1. **统一智能模式，无模式开关。** 「简单任务无步骤展示，直接给结果」（§9.1）
 *      ——本回合没有任何工具调用时（纯文本答复 / 快档直答），`visibleSteps` 为空，
 *      本组件**不渲染任何东西**，气泡里只剩正文。复杂度对用户不可见。
 *   2. **三态**：`status === null` = 进行中、`'ok'` = 完成、`'error'` = 失败。
 *      三态是**流式期间**的概念——收尾后投影整份释放（03b），气泡让位给正式消息。
 *   3. **`tool_result` 只显示摘要，不含原始数据**（§8.1）。服务端 `tool_result`
 *      帧本来就只有「成了没有」+ 失败时的安全枚举码（`stream.py` 的
 *      `summarize_outcome`），读工具的整块账本数据从不进这条通道。
 *
 * 折叠的粒度是**整条步骤流**而非单张卡：单卡没有可展开的内容——`tool_result`
 * 的载荷就是一行摘要，展开也只有一个「空」。所以「可折叠」落在步骤区的标题行上。
 *
 * `routing` 阶段的**空态占位**：该阶段在 P2 没有生产者（域路由归 P4 的 05g），
 * 这里先把行占好位——05g 一旦发帧，步骤区直接显示「正在理解你的需求…」，
 * 不必再改一次渲染。反向也成立：没有该帧时不会凭空多出一行。
 */

import { useState } from 'react'
import type { AgentStepView } from './use-assistant-stream'

/** `routing` 阶段的占位文案（生产者归 P4 的 05g）。 */
const ROUTING_PLACEHOLDER = '正在理解你的需求…'

/**
 * 工具失败的**安全枚举码** → 用户可读文案。
 *
 * 码集来自服务端 `tools.py`（`ERR_*` 八个）。**不能直接显示服务端 `message`**：
 * 那是回喂给**模型**的指令（如「请先查询分类清单再使用完全一致的名称」，
 * 甚至「请向用户说明这一情况并请其指明」），读给用户是错的。故这里按码另写
 * 用户向文案；未知码走兜底，绝不透传任何服务端原文。
 */
const STEP_ERROR_ZH: Record<string, string> = {
  unknown_tool: '这一步没有可用的处理方式',
  invalid_arguments: '这一步的参数不正确',
  unknown_category: '分类不存在，请先创建或选择分类',
  unknown_account: '账户不存在',
  ambiguous_account: '有多个同名账户，无法确定是哪一个',
  unknown_book: '账本不存在',
  ambiguous_book: '有多个同名账本，无法确定是哪一个',
  invalid_period: '时间范围不正确',
}

/** 失败态的兜底文案（码缺失或未知）。 */
const STEP_ERROR_FALLBACK = '这一步没有完成'

type StepStatus = 'running' | 'ok' | 'error'

/** 单步的三态：`tool_result` 未到 = 进行中；`ok` 完成；`error` 失败。 */
function stepStatus(s: AgentStepView): StepStatus {
  if (s.status === 'ok') return 'ok'
  if (s.status === 'error') return 'error'
  return 'running'
}

/**
 * 步骤区实际展示哪些步。
 *
 * - 有工具名的步（`tool_call` / `tool_result` 到过）——步骤卡片的主体；
 * - `routing` 阶段的步（P2 无生产者，占位）；
 * - **只有 `phase: thinking` 的步不展示**：它没有人类可读的载荷，单独成行是噪音
 *   （正是「简单任务不展示步骤」在单步内的对应物）。
 */
function visibleSteps(steps: AgentStepView[]): AgentStepView[] {
  return steps.filter(s => s.tool !== null || s.phase === 'routing')
}

/**
 * 一行的文案。三态各取一处来源：
 * - 进行中：服务端的进度句（`tool_call.message`，如「正在查询预算…」）；
 * - 完成：把进度句的首字「正在」换成「已」——服务端**不发**成功摘要
 *   （§8.1 只给「成了没有」），这是唯一不需要新协议就能读通的写法；
 * - 失败：安全枚举码 → 用户向文案（见 `STEP_ERROR_ZH`）。
 */
function stepText(s: AgentStepView): string {
  if (stepStatus(s) === 'error') {
    return (s.code && STEP_ERROR_ZH[s.code]) || STEP_ERROR_FALLBACK
  }
  if (s.phase === 'routing' && !s.message) return ROUTING_PLACEHOLDER
  const msg = s.message
  if (!msg) return s.tool ?? ROUTING_PLACEHOLDER
  if (stepStatus(s) === 'ok' && msg.startsWith('正在')) return '已' + msg.slice(2)
  return msg
}

const STATUS_ICON: Record<StepStatus, string> = {
  running: '⏳',
  ok: '✓',
  error: '✕',
}

const STATUS_COLOR: Record<StepStatus, string> = {
  running: 'var(--ink2)',
  ok: 'var(--ink3)',
  error: 'var(--expense)',
}

export interface StepCardStreamProps {
  /** 投影里的步骤（`AgentStreamProjection.steps`），按到达顺序 */
  steps: AgentStepView[]
}

/**
 * 步骤区。**空则什么都不渲染**（统一智能模式：简单任务无步骤展示）。
 *
 * 默认展开——步骤区存在的理由就是「长任务时让用户看见在做什么」；折叠是用户
 * 主动收起的选择，不跨轮保留（一轮结束气泡即卸载）。
 */
export function StepCardStream({ steps }: StepCardStreamProps) {
  const [open, setOpen] = useState(true)
  const rows = visibleSteps(steps)
  if (rows.length === 0) return null

  return (
    <div className="mb-1" data-testid="step-stream">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        className="flex items-center gap-1 text-xs cursor-pointer select-none"
        style={{ color: 'var(--ink3)' }}
        data-testid="step-stream-toggle"
      >
        <span aria-hidden>{open ? '▾' : '▸'}</span>
        <span>{`步骤 · ${rows.length} 步`}</span>
      </button>

      {open && (
        <ul className="mt-1 space-y-1" data-testid="step-stream-list">
          {rows.map(s => {
            const status = stepStatus(s)
            return (
              <li
                key={s.step}
                className="flex items-start gap-1.5 text-xs"
                style={{ color: STATUS_COLOR[status] }}
                data-testid={`step-row-${s.step}`}
                data-status={status}
              >
                <span aria-hidden className="shrink-0">{STATUS_ICON[status]}</span>
                <span className="flex-1 whitespace-pre-wrap">{stepText(s)}</span>
                {/* 档位徽标：`phase.reasoning === 'deep'`（服务端只在该步起点那一帧给） */}
                {s.reasoning === 'deep' && (
                  <span
                    className="shrink-0"
                    style={{ color: 'var(--ink3)' }}
                    data-testid={`step-deep-${s.step}`}
                  >
                    深度思考
                  </span>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}