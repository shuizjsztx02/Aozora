/**
 * 回放页右侧事件时间轴。
 *
 * - 按 `seq` 升序渲染（后端已排序）；
 * - `assistant.tool_calls` 与对应的 `tool` 结果按 `tool_call_id` 配对成组，
 *   结果事件不再单独重复渲染；配对不到时如实提示；
 * - `assistant.reasoning_content` 默认折叠；
 * - `quota` / `error` / `replace` / `approval_request` / `approval_result`
 *   语义化展示（审批四值 + 来源标签）。
 */

import type { EventItem } from '../api/replay'
import { cn } from '../lib/cn'
import { formatBeijing } from '../lib/time'
import { buttonSecondaryClass } from '../lib/ui'
import { JsonViewer } from './JsonViewer'

interface EventTimelineProps {
  events: EventItem[]
  turn: number | null
  reveal: boolean
  /** 当前轮次是否还有下一页事件（用于区分「未上报」与「未在当前页」）。 */
  hasMore: boolean
  isLoading: boolean
  error: string | null
  onRetry: () => void
}

interface ToolCallInfo {
  id: string
  name: string
  argumentsText: string
}

type TimelineEntry =
  | { kind: 'toolGroup'; event: EventItem; calls: ToolCallInfo[]; results: Map<string, EventItem> }
  | { kind: 'event'; event: EventItem }

const TYPE_META: Record<string, { label: string; badge: string; dot: string }> = {
  user: { label: '用户', badge: 'bg-blue-50 text-blue-700 ring-blue-200', dot: 'bg-blue-400' },
  assistant: {
    label: '助手',
    badge: 'bg-gray-100 text-gray-700 ring-gray-200',
    dot: 'bg-gray-400',
  },
  tool: {
    label: '工具结果',
    badge: 'bg-slate-100 text-slate-700 ring-slate-200',
    dot: 'bg-slate-400',
  },
  quota: {
    label: '用量',
    badge: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
    dot: 'bg-emerald-400',
  },
  error: { label: '错误', badge: 'bg-red-50 text-red-700 ring-red-200', dot: 'bg-red-500' },
  replace: {
    label: '摘要替换',
    badge: 'bg-amber-50 text-amber-700 ring-amber-200',
    dot: 'bg-amber-400',
  },
  approval_request: {
    label: '审批请求',
    badge: 'bg-indigo-50 text-indigo-700 ring-indigo-200',
    dot: 'bg-indigo-400',
  },
  approval_result: {
    label: '审批结果',
    badge: 'bg-purple-50 text-purple-700 ring-purple-200',
    dot: 'bg-purple-400',
  },
}

const FALLBACK_META = {
  label: '事件',
  badge: 'bg-gray-100 text-gray-600 ring-gray-200',
  dot: 'bg-gray-300',
}

const APPROVAL_RESULT_META: Record<string, { label: string; className: string }> = {
  'allowed-once': {
    label: 'allowed-once（允许一次）',
    className: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  },
  rejected: { label: 'rejected（已拒绝）', className: 'bg-red-50 text-red-700 ring-red-200' },
  cancelled: { label: 'cancelled（已取消）', className: 'bg-gray-100 text-gray-600 ring-gray-200' },
  unavailable: {
    label: 'unavailable（不可用）',
    className: 'bg-amber-50 text-amber-700 ring-amber-200',
  },
}

const CONFIRM_REASON_LABELS: Record<string, string> = {
  high_risk: '高风险操作',
  medium_risk: '中风险操作（会改动既有数据）',
  multi_step: '多步操作',
  low_confidence: '低置信度',
}

const RISK_META: Record<string, string> = {
  low: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  medium: 'bg-amber-50 text-amber-700 ring-amber-200',
  high: 'bg-red-50 text-red-700 ring-red-200',
}

export function EventTimeline({
  events,
  turn,
  reveal,
  hasMore,
  isLoading,
  error,
  onRetry,
}: EventTimelineProps) {
  if (error) {
    return (
      <div className="rounded-lg border border-gray-200 bg-white px-3 py-8 text-center">
        <p className="text-sm text-red-600">{error}</p>
        <button type="button" className={cn(buttonSecondaryClass, 'mt-2')} onClick={onRetry}>
          重试
        </button>
      </div>
    )
  }

  if (isLoading && events.length === 0) {
    return (
      <div className="rounded-lg border border-gray-200 bg-white px-3 py-8 text-center text-sm text-gray-500">
        加载中…
      </div>
    )
  }

  if (events.length === 0) {
    return (
      <div className="rounded-lg border border-gray-200 bg-white px-3 py-8 text-center text-sm text-gray-500">
        {turn === null ? '选择左侧轮次查看事件' : `第 ${turn} 轮没有事件`}
      </div>
    )
  }

  const entries = buildEntries(events)
  const approvalResults = collectApprovalResults(events)

  return (
    <div className={cn('space-y-2', isLoading && 'opacity-60')}>
      {reveal ? (
        <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          当前展示原文（reveal=true）。本次查看已记录审计。
        </p>
      ) : null}
      <ol className="space-y-3">
        {entries.map((entry) =>
          entry.kind === 'toolGroup' ? (
            <ToolGroupItem
              key={`group-${entry.event.seq}`}
              entry={entry}
              hasMore={hasMore}
            />
          ) : (
            <EventItemCard
              key={`event-${entry.event.seq}`}
              event={entry.event}
              approvalResults={approvalResults}
              hasMore={hasMore}
            />
          ),
        )}
      </ol>
      {isLoading ? <p className="text-right text-xs text-gray-400">加载中…</p> : null}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 配对
// ---------------------------------------------------------------------------

function buildEntries(events: EventItem[]): TimelineEntry[] {
  const results = new Map<string, EventItem>()
  for (const event of events) {
    if (event.type !== 'tool') continue
    const callId = asString(event.data.tool_call_id)
    if (callId) results.set(callId, event)
  }

  const consumed = new Set<number>()
  const entries: TimelineEntry[] = []
  for (const event of events) {
    if (consumed.has(event.seq)) continue
    const calls = event.type === 'assistant' ? extractToolCalls(event.data) : []
    if (calls.length === 0) {
      entries.push({ kind: 'event', event })
      continue
    }
    const matched = new Map<string, EventItem>()
    for (const call of calls) {
      const result = results.get(call.id)
      if (result) {
        matched.set(call.id, result)
        consumed.add(result.seq)
      }
    }
    entries.push({ kind: 'toolGroup', event, calls, results: matched })
  }
  return entries
}

function collectApprovalResults(events: EventItem[]): Map<string, EventItem> {
  const results = new Map<string, EventItem>()
  for (const event of events) {
    if (event.type !== 'approval_result') continue
    const proposalId = asString(event.data.proposalId)
    if (proposalId) results.set(proposalId, event)
  }
  return results
}

function extractToolCalls(data: Record<string, unknown>): ToolCallInfo[] {
  const raw = data.tool_calls
  if (!Array.isArray(raw)) return []
  return raw.map((entry, index) => {
    const record = isRecord(entry) ? entry : {}
    const fn = isRecord(record.function) ? record.function : {}
    const args = fn.arguments
    return {
      id: asString(record.id) ?? `#${index + 1}`,
      name: asString(fn.name) ?? '未知工具',
      argumentsText: typeof args === 'string' ? args : JSON.stringify(args ?? {}, null, 2),
    }
  })
}

// ---------------------------------------------------------------------------
// 渲染
// ---------------------------------------------------------------------------

function EventItemCard({
  event,
  approvalResults,
  hasMore,
}: {
  event: EventItem
  approvalResults: Map<string, EventItem>
  hasMore: boolean
}) {
  const meta = TYPE_META[event.type] ?? FALLBACK_META
  return (
    <li className="relative border-l-2 border-gray-100 pl-4">
      <span
        aria-hidden="true"
        className={cn('absolute -left-[5px] top-2 h-2 w-2 rounded-full', meta.dot)}
      />
      <div className="rounded-md border border-gray-200 bg-white px-3 py-2">
        <EventHeader event={event} meta={meta} />
        <EventBody event={event} approvalResults={approvalResults} hasMore={hasMore} />
      </div>
    </li>
  )
}

function EventHeader({
  event,
  meta,
}: {
  event: EventItem
  meta: { label: string; badge: string }
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
      <span className="font-mono text-gray-400">#{event.seq}</span>
      <span
        className={cn(
          'rounded px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset',
          meta.badge,
        )}
      >
        {meta.label}
      </span>
      <span>{formatBeijing(event.time)}</span>
      {event.request_id ? (
        <span className="font-mono text-gray-400">req {event.request_id}</span>
      ) : null}
    </div>
  )
}

function EventBody({
  event,
  approvalResults,
  hasMore,
}: {
  event: EventItem
  approvalResults: Map<string, EventItem>
  hasMore: boolean
}) {
  switch (event.type) {
    case 'user':
      return <UserBody event={event} />
    case 'assistant':
      return <AssistantBody event={event} />
    case 'tool':
      return <StandaloneToolBody event={event} />
    case 'quota':
      return <QuotaBody event={event} />
    case 'error':
      return <ErrorBody event={event} />
    case 'replace':
      return <ReplaceBody event={event} />
    case 'approval_request':
      return (
        <ApprovalRequestBody
          event={event}
          approvalResults={approvalResults}
          hasMore={hasMore}
        />
      )
    case 'approval_result':
      return <ApprovalResultBody event={event} />
    default:
      return <JsonViewer value={event.data} label="事件载荷" className="mt-1" />
  }
}

function UserBody({ event }: { event: EventItem }) {
  const content = asString(event.data.content)
  return (
    <p className="mt-1 whitespace-pre-wrap break-words text-sm text-gray-800">
      {content || '（空消息）'}
    </p>
  )
}

function AssistantBody({ event }: { event: EventItem }) {
  const content = asString(event.data.content)
  const reasoning = asString(event.data.reasoning_content)
  return (
    <div className="mt-1 space-y-1">
      {content ? (
        <p className="whitespace-pre-wrap break-words text-sm text-gray-800">{content}</p>
      ) : null}
      {reasoning ? (
        <details className="rounded-md border border-gray-200 bg-gray-50 px-2 py-1">
          <summary className="cursor-pointer select-none text-xs text-gray-500">
            推理过程（默认折叠）
          </summary>
          <div className="mt-1 whitespace-pre-wrap break-words text-xs leading-relaxed text-gray-600">
            {reasoning}
          </div>
        </details>
      ) : null}
      {!content && !reasoning ? (
        <p className="text-xs text-gray-400">（该条消息仅有工具调用）</p>
      ) : null}
    </div>
  )
}

function StandaloneToolBody({ event }: { event: EventItem }) {
  const callId = asString(event.data.tool_call_id)
  const content = asString(event.data.content) ?? ''
  return (
    <div className="mt-1 space-y-1">
      <div className="text-xs text-gray-500">
        tool_call_id <span className="font-mono">{callId ?? '—'}</span>
      </div>
      <PayloadText text={content} />
    </div>
  )
}

function QuotaBody({ event }: { event: EventItem }) {
  const provider = asString(event.data.provider) ?? '—'
  const model = asString(event.data.model) ?? '—'
  const tokens = asNumber(event.data.tokens)
  const promptTokens = asNumber(event.data.prompt_tokens)
  const completionTokens = asNumber(event.data.completion_tokens)
  return (
    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
      <span className="rounded bg-emerald-50 px-1.5 py-0.5 font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200">
        {model}
      </span>
      <span className="text-gray-500">{provider}</span>
      {tokens !== null ? (
        <span className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-gray-700">
          {tokens.toLocaleString()} tokens
        </span>
      ) : null}
      {promptTokens !== null || completionTokens !== null ? (
        <span className="text-gray-400">
          prompt {promptTokens ?? '—'} / completion {completionTokens ?? '—'}
        </span>
      ) : null}
    </div>
  )
}

function ErrorBody({ event }: { event: EventItem }) {
  const code = asString(event.data.code) ?? 'unknown'
  const message = asString(event.data.message) ?? ''
  return (
    <div className="mt-1 rounded-md border border-red-200 bg-red-50 px-2 py-1.5 text-sm text-red-700">
      <span className="font-mono text-xs font-medium">{code}</span>
      {message ? <span className="ml-2">{message}</span> : null}
    </div>
  )
}

function ReplaceBody({ event }: { event: EventItem }) {
  const uptoTurn = asNumber(event.data.upto_turn)
  const compressedTurns = asNumber(event.data.turns)
  const summary = asString(event.data.summary)
  return (
    <div className="mt-1 rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-sm text-amber-800">
      <div>
        摘要替换位置：覆盖至第 {uptoTurn ?? '—'} 轮
        {compressedTurns !== null ? `（本次压缩 ${compressedTurns} 轮）` : ''}
      </div>
      {summary ? (
        <details className="mt-1">
          <summary className="cursor-pointer select-none text-xs text-amber-700">
            查看摘要文本
          </summary>
          <p className="mt-1 whitespace-pre-wrap break-words text-xs leading-relaxed">
            {summary}
          </p>
        </details>
      ) : null}
    </div>
  )
}

function ApprovalRequestBody({
  event,
  approvalResults,
  hasMore,
}: {
  event: EventItem
  approvalResults: Map<string, EventItem>
  hasMore: boolean
}) {
  const proposalId = asString(event.data.proposalId)
  const op = asString(event.data.op) ?? '—'
  const risk = asString(event.data.risk)
  const confirmReason = asString(event.data.confirmReason)
  const step = asNumber(event.data.step)
  const result = proposalId ? approvalResults.get(proposalId) : undefined

  return (
    <div className="mt-1 space-y-1 rounded-md border border-indigo-200 bg-indigo-50 px-2 py-1.5 text-sm text-indigo-900">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        <span>
          op <span className="font-mono font-medium">{op}</span>
        </span>
        <span className="inline-flex items-center gap-1">
          risk <RiskBadge risk={risk} />
        </span>
        <span>
          理由 <span className="font-medium">{confirmReasonLabel(confirmReason)}</span>
        </span>
        {step !== null ? <span>Step {step}</span> : null}
      </div>
      <div className="text-xs text-indigo-700">
        提案 <span className="font-mono">{proposalId ?? '—'}</span>
      </div>
      <ApprovalStatus result={result} hasMore={hasMore} />
    </div>
  )
}

function ApprovalStatus({
  result,
  hasMore,
}: {
  result: EventItem | undefined
  hasMore: boolean
}) {
  if (!result) {
    return (
      <div className="text-xs text-gray-600">
        结果：{hasMore ? '未在当前页（可翻页查看）' : '未上报'}
      </div>
    )
  }
  const value = asString(result.data.result) ?? 'unknown'
  const source = asString(result.data.source)
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-gray-700">
      <span>结果：</span>
      <ApprovalResultBadge value={value} />
      <ApprovalSourceBadge source={source} />
    </div>
  )
}

function ApprovalResultBody({ event }: { event: EventItem }) {
  const value = asString(event.data.result) ?? 'unknown'
  const source = asString(event.data.source)
  const reason = asString(event.data.reason)
  const proposalId = asString(event.data.proposalId)
  return (
    <div className="mt-1 flex flex-wrap items-center gap-2 text-sm">
      <ApprovalResultBadge value={value} />
      <ApprovalSourceBadge source={source} />
      {reason ? <span className="text-xs text-gray-500">原因：{reason}</span> : null}
      {proposalId ? (
        <span className="text-xs text-gray-400">
          提案 <span className="font-mono">{proposalId}</span>
        </span>
      ) : null}
    </div>
  )
}

function ToolGroupItem({
  entry,
  hasMore,
}: {
  entry: Extract<TimelineEntry, { kind: 'toolGroup' }>
  hasMore: boolean
}) {
  return (
    <li className="relative border-l-2 border-slate-200 pl-4">
      <span aria-hidden="true" className="absolute -left-[5px] top-2 h-2 w-2 rounded-full bg-slate-400" />
      <div className="rounded-md border border-slate-200 bg-slate-50/60 px-3 py-2">
        <EventHeader event={entry.event} meta={TYPE_META.assistant} />
        <AssistantBody event={entry.event} />
        <div className="mt-2 space-y-2">
          {entry.calls.map((call) => {
            const result = entry.results.get(call.id)
            return (
              <div
                key={`${call.id}-${call.name}`}
                className="rounded-md border border-gray-200 bg-white px-2 py-1.5"
              >
                <div className="flex flex-wrap items-center gap-2 text-xs text-gray-600">
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono font-medium text-slate-700">
                    {call.name}
                  </span>
                  <span className="font-mono text-gray-400">{call.id}</span>
                </div>
                <JsonViewer
                  value={safeParse(call.argumentsText)}
                  label="调用参数"
                  className="mt-1"
                />
                {result ? (
                  <div className="mt-1">
                    <div className="text-[11px] font-medium text-gray-500">工具结果</div>
                    <PayloadText text={asString(result.data.content) ?? ''} />
                  </div>
                ) : (
                  <p className="mt-1 text-xs text-amber-700">
                    未在当前页找到结果
                    {hasMore ? '（可翻页查看）' : '（可能已被保留期清理）'}
                  </p>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </li>
  )
}

function ApprovalResultBadge({ value }: { value: string }) {
  const meta = APPROVAL_RESULT_META[value] ?? {
    label: value,
    className: 'bg-gray-100 text-gray-600 ring-gray-200',
  }
  return (
    <span
      className={cn(
        'rounded px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset',
        meta.className,
      )}
    >
      {meta.label}
    </span>
  )
}

function ApprovalSourceBadge({ source }: { source: string | null }) {
  if (source === 'server') {
    return (
      <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-xs font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200">
        服务端确认
      </span>
    )
  }
  if (source === 'client') {
    return (
      <span className="rounded bg-amber-50 px-1.5 py-0.5 text-xs font-medium text-amber-700 ring-1 ring-inset ring-amber-200">
        客户端上报
      </span>
    )
  }
  return <span className="text-xs text-gray-400">来源未知</span>
}

function RiskBadge({ risk }: { risk: string | null }) {
  if (!risk) return <span className="text-xs text-gray-400">—</span>
  return (
    <span
      className={cn(
        'rounded px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset',
        RISK_META[risk] ?? 'bg-gray-100 text-gray-600 ring-gray-200',
      )}
    >
      {risk}
    </span>
  )
}

function PayloadText({ text }: { text: string }) {
  return (
    <pre className="mt-1 max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-md border border-gray-200 bg-gray-50 px-2 py-1.5 font-mono text-xs leading-relaxed text-gray-700">
      {prettyJson(text)}
    </pre>
  )
}

// ---------------------------------------------------------------------------
// 工具函数
// ---------------------------------------------------------------------------

function confirmReasonLabel(reason: string | null): string {
  if (!reason) return '无'
  return CONFIRM_REASON_LABELS[reason] ?? reason
}

function safeParse(text: string): unknown {
  const trimmed = text.trim()
  if (!trimmed) return {}
  try {
    return JSON.parse(trimmed) as unknown
  } catch {
    return text
  }
}

function prettyJson(text: string): string {
  const trimmed = text.trim()
  if (!trimmed) return '（空）'
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2) ?? trimmed
  } catch {
    return text
  }
}

function asString(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
