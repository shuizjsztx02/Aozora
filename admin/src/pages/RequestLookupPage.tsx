/**
 * T-M8-08：`request_id` 精确反查页。
 *
 * 数据源：`GET /admin/requests/{request_id}`（`server/src/modules/admin/routes.py:455`）。
 * 服务端按 `ai_conversation_events.request_id` 精确命中，并兼容
 * `ai_usage.request_id`；两者都为空时返回 404 `request_not_found`。
 */

import { useState, type FormEvent, type ReactNode } from 'react'
import { ApiError } from '../api/client'
import {
  useRequestLookupQuery,
  type RequestConversationHit,
  type RequestLookupResponse,
  type RequestUsageItem,
} from '../api/usage'
import { MonoId } from '../components/MonoId'
import { cn } from '../lib/cn'
import { describeError } from '../lib/errors'
import { formatBeijing } from '../lib/time'
import { buttonPrimaryClass, buttonSecondaryClass, inputClass } from '../lib/ui'

export function RequestLookupPage() {
  const [input, setInput] = useState('')
  const [applied, setApplied] = useState<string | null>(null)
  const [hint, setHint] = useState<string | null>(null)
  const query = useRequestLookupQuery(applied)

  function submit() {
    const requestId = input.trim()
    if (!requestId) {
      setHint('请输入 request_id')
      return
    }
    setHint(null)
    if (requestId === applied) {
      // 重复提交同一 ID（例如上次失败）时显式重取。
      void query.refetch()
      return
    }
    setApplied(requestId)
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    submit()
  }

  function reset() {
    setInput('')
    setApplied(null)
    setHint(null)
  }

  return (
    <section className="space-y-3">
      <h1 className="text-lg font-semibold text-gray-900">request_id 反查</h1>

      <form onSubmit={handleSubmit} className="rounded-lg border border-gray-200 bg-white p-4">
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs font-medium text-gray-600">
            request_id（精确匹配事件表与 ai_usage）
          </span>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              className={inputClass}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="例如 5f2c9a1e… （客户端 X-Request-Id）"
              autoComplete="off"
              spellCheck={false}
            />
            <div className="flex shrink-0 gap-2">
              <button
                type="submit"
                className={buttonPrimaryClass}
                disabled={query.isFetching}
              >
                {query.isFetching ? '查询中…' : '查询'}
              </button>
              <button
                type="button"
                className={buttonSecondaryClass}
                onClick={reset}
                disabled={query.isFetching}
              >
                重置
              </button>
            </div>
          </div>
        </label>
        {hint ? <p className="mt-2 text-sm text-red-600">{hint}</p> : null}
      </form>

      {!applied ? (
        <NoticeCard
          title="等待查询"
          body="反查精确匹配 ai_conversation_events.request_id 与 ai_usage.request_id 两列；历史事件（M8-03 之前）该列为 NULL，无法通过 request_id 命中原事件。"
        />
      ) : query.error ? (
        isNotFound(query.error) ? (
          <NoticeCard
            title="未找到该 request_id"
            body="服务端事件表与 ai_usage 均无该 request_id 的记录（404 request_not_found）。可能是 ID 输入有误，或该请求属于尚未写入 request_id 的历史事件。"
          />
        ) : (
          <div className="rounded-lg border border-gray-200 bg-white p-6">
            <p className="text-sm text-red-600">{describeError(query.error)}</p>
            <button
              type="button"
              className={cn(buttonSecondaryClass, 'mt-3')}
              onClick={() => void query.refetch()}
            >
              重试
            </button>
          </div>
        )
      ) : query.isPending ? (
        <div className="rounded-lg border border-gray-200 bg-white p-6 text-sm text-gray-500">
          加载中…
        </div>
      ) : query.data ? (
        <ResultSection data={query.data} />
      ) : null}
    </section>
  )
}

function isNotFound(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    (error.status === 404 || error.code === 'request_not_found')
  )
}

/** 成功结果：来源标识 + 概览 + 会话/轮次 + 用量明细。 */
function ResultSection({ data }: { data: RequestLookupResponse }) {
  const hasEvents = data.conversations.length > 0
  const hasUsage = data.usage.length > 0
  const eventCount = data.conversations.reduce((sum, row) => sum + row.event_count, 0)

  const turnKeys = new Set<string>()
  for (const row of data.conversations) {
    for (const turn of row.turns) turnKeys.add(`${row.conversation_id}:${turn}`)
  }

  const usageTokens = data.usage.reduce((sum, row) => sum + row.tokens, 0)
  const usageCost = data.usage.reduce((sum, row) => sum + row.cost, 0)

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-gray-200 bg-white p-4">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-gray-500">request_id</span>
          <code className="break-all font-mono text-xs text-gray-900">{data.request_id}</code>
          <span className="flex flex-wrap gap-1.5 sm:ml-auto">
            {hasEvents ? <SourceBadge tone="blue">事件表精确命中</SourceBadge> : null}
            {hasUsage ? <SourceBadge tone="green">ai_usage 命中</SourceBadge> : null}
            {!hasEvents && hasUsage ? (
              <SourceBadge tone="amber">仅 ai_usage（无事件行）</SourceBadge>
            ) : null}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="命中会话" value={data.conversations.length} />
        <StatCard label="命中轮次" value={turnKeys.size} />
        <StatCard label="事件数" value={eventCount} />
        <StatCard label="用量 tokens" value={usageTokens} />
      </div>

      <h2 className="text-base font-semibold text-gray-900">命中会话与轮次</h2>
      {hasEvents ? (
        <ConversationHitsTable rows={data.conversations} />
      ) : (
        <NoticeCard
          title="仅 ai_usage 命中"
          body="事件表没有该 request_id 的行。ai_conversation_events.request_id 自 M8-03 起写入，历史事件为 NULL，因此无法反查其会话与轮次。"
        />
      )}

      <h2 className="text-base font-semibold text-gray-900">用量记录</h2>
      {hasUsage ? (
        <UsageTable rows={data.usage} totalTokens={usageTokens} totalCost={usageCost} />
      ) : (
        <NoticeCard
          title="无 ai_usage 记录"
          body="该 request_id 仅命中事件表，ai_usage 没有对应行（例如未产生 token 记录的失败请求）。"
        />
      )}

      <p className="text-xs text-gray-500">
        事件类型为命中事件的类型集合（后端未提供按类型计数）；轮次按会话内的 turn
        去重列出。时间统一按北京时间（Asia/Shanghai）显示；成本单位微元（1e-6
        元），按 ai_usage 原值展示、不做换算。会话 ID 为「—（快路径）」表示该用量行来自非
        agent 快路径，服务端未关联会话。
      </p>
    </div>
  )
}

function ConversationHitsTable({ rows }: { rows: RequestConversationHit[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
      <table className="w-full min-w-[960px] border-collapse text-left text-sm">
        <thead className="bg-gray-50 text-gray-600">
          <tr>
            <th scope="col" className="whitespace-nowrap border-b border-gray-200 px-3 py-2 font-medium">
              会话 ID
            </th>
            <th scope="col" className="whitespace-nowrap border-b border-gray-200 px-3 py-2 font-medium">
              用户 ID
            </th>
            <th scope="col" className="whitespace-nowrap border-b border-gray-200 px-3 py-2 font-medium">
              轮次
            </th>
            <th scope="col" className="whitespace-nowrap border-b border-gray-200 px-3 py-2 text-right font-medium">
              事件数
            </th>
            <th scope="col" className="whitespace-nowrap border-b border-gray-200 px-3 py-2 text-right font-medium">
              seq 区间
            </th>
            <th scope="col" className="whitespace-nowrap border-b border-gray-200 px-3 py-2 font-medium">
              事件类型
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.conversation_id} className="border-b border-gray-100 last:border-b-0">
              <td className="whitespace-nowrap px-3 py-2 align-top">
                <MonoId value={row.conversation_id} />
              </td>
              <td className="whitespace-nowrap px-3 py-2 align-top">
                {row.user_id ? (
                  <MonoId value={row.user_id} />
                ) : (
                  <span className="text-xs text-gray-400">—</span>
                )}
              </td>
              <td className="px-3 py-2 align-top font-mono text-xs text-gray-800">
                {row.turns.length > 0 ? row.turns.join('、') : '—'}
                <span className="ml-1 text-gray-400">（{row.turns.length} 轮）</span>
              </td>
              <td className="whitespace-nowrap px-3 py-2 text-right align-top font-mono tabular-nums text-gray-800">
                {row.event_count}
              </td>
              <td className="whitespace-nowrap px-3 py-2 text-right align-top font-mono tabular-nums text-gray-800">
                {row.first_seq}–{row.last_seq}
              </td>
              <td className="px-3 py-2 align-top">
                <EventTypeChips types={row.event_types} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function UsageTable({
  rows,
  totalTokens,
  totalCost,
}: {
  rows: RequestUsageItem[]
  totalTokens: number
  totalCost: number
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
      <table className="w-full min-w-[880px] border-collapse text-left text-sm">
        <thead className="bg-gray-50 text-gray-600">
          <tr>
            <th scope="col" className="whitespace-nowrap border-b border-gray-200 px-3 py-2 font-medium">
              provider
            </th>
            <th scope="col" className="whitespace-nowrap border-b border-gray-200 px-3 py-2 font-medium">
              model
            </th>
            <th scope="col" className="whitespace-nowrap border-b border-gray-200 px-3 py-2 text-right font-medium">
              tokens
            </th>
            <th scope="col" className="whitespace-nowrap border-b border-gray-200 px-3 py-2 text-right font-medium">
              成本（微元）
            </th>
            <th scope="col" className="whitespace-nowrap border-b border-gray-200 px-3 py-2 font-medium">
              会话 ID
            </th>
            <th scope="col" className="whitespace-nowrap border-b border-gray-200 px-3 py-2 font-medium">
              时间（北京时间）
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            // 同一 request_id 下可能存在同 provider/model 的多行，用序号兜底唯一 key。
            <tr
              key={`${index}:${row.provider}:${row.model}:${row.created_at}`}
              className="border-b border-gray-100 last:border-b-0"
            >
              <td className="whitespace-nowrap px-3 py-2 align-top font-mono text-xs text-gray-800">
                {row.provider}
              </td>
              <td className="whitespace-nowrap px-3 py-2 align-top font-mono text-xs text-gray-800">
                {row.model}
              </td>
              <td className="whitespace-nowrap px-3 py-2 text-right align-top font-mono tabular-nums text-gray-800">
                {row.tokens}
              </td>
              <td className="whitespace-nowrap px-3 py-2 text-right align-top font-mono tabular-nums text-gray-800">
                {row.cost}
              </td>
              <td className="whitespace-nowrap px-3 py-2 align-top">
                {row.conversation_id ? (
                  <MonoId value={row.conversation_id} />
                ) : (
                  <span className="text-xs text-gray-400" title="快路径用量行，服务端未关联会话">
                    —（快路径）
                  </span>
                )}
              </td>
              <td className="whitespace-nowrap px-3 py-2 align-top font-mono text-xs tabular-nums text-gray-800">
                {formatBeijing(row.created_at)}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot className="bg-gray-50 font-medium text-gray-900">
          <tr>
            <td className="whitespace-nowrap border-t border-gray-200 px-3 py-2" colSpan={2}>
              合计（{rows.length} 行）
            </td>
            <td className="whitespace-nowrap border-t border-gray-200 px-3 py-2 text-right font-mono tabular-nums">
              {totalTokens}
            </td>
            <td className="whitespace-nowrap border-t border-gray-200 px-3 py-2 text-right font-mono tabular-nums">
              {totalCost}
            </td>
            <td className="border-t border-gray-200 px-3 py-2" colSpan={2} />
          </tr>
        </tfoot>
      </table>
    </div>
  )
}

function EventTypeChips({ types }: { types: string[] }) {
  if (types.length === 0) return <span className="text-xs text-gray-400">—</span>
  return (
    <span className="flex flex-wrap gap-1">
      {types.map((type) => (
        <span
          key={type}
          className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-xs text-gray-700"
        >
          {type}
        </span>
      ))}
    </span>
  )
}

type BadgeTone = 'blue' | 'green' | 'amber'

const BADGE_TONE_CLASS: Record<BadgeTone, string> = {
  blue: 'bg-blue-100 text-blue-700',
  green: 'bg-green-100 text-green-700',
  amber: 'bg-amber-100 text-amber-700',
}

function SourceBadge({ tone, children }: { tone: BadgeTone; children: ReactNode }) {
  return (
    <span
      className={cn(
        'inline-flex rounded px-1.5 py-0.5 text-xs font-medium',
        BADGE_TONE_CLASS[tone],
      )}
    >
      {children}
    </span>
  )
}

function StatCard({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white px-3 py-2">
      <div className="text-xs text-gray-500">{label}</div>
      <div className="mt-0.5 font-mono text-lg tabular-nums text-gray-900">{value}</div>
    </div>
  )
}

function NoticeCard({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-6">
      <p className="text-sm font-medium text-gray-800">{title}</p>
      <p className="mt-1 text-sm text-gray-500">{body}</p>
    </div>
  )
}
