/**
 * T-M8-08：AI 用量聚合页。
 *
 * 数据源：`GET /admin/ai-usage/summary?group_by=user|model|day&time_from&time_to`
 * （`server/src/modules/admin/routes.py:433`）。只展示 `ai_usage` 的聚合行，
 * 不 JOIN 会话事件；`cost` 与 `tokens` 均为后端原值，前端不做换算。
 */

import { useMemo, useState } from 'react'
import { useUsageSummaryQuery, type UsageGroup, type UsageGroupBy } from '../api/usage'
import { MonoId } from '../components/MonoId'
import { FilterBar, FilterField } from '../components/FilterBar'
import { cn } from '../lib/cn'
import { describeError } from '../lib/errors'
import { beijingInputToIso } from '../lib/time'
import { buttonSecondaryClass, inputClass } from '../lib/ui'

const GROUP_OPTIONS: { value: UsageGroupBy; label: string; keyLabel: string }[] = [
  { value: 'user', label: '按用户', keyLabel: '用户 ID' },
  { value: 'model', label: '按模型', keyLabel: '模型' },
  { value: 'day', label: '按天', keyLabel: '天（服务端 date(created_at)）' },
]

const EMPTY_FORM = { timeFrom: '', timeTo: '' }

/** 合计行——users 只在按用户分组时可直接相加（每组即一个用户）。 */
interface UsageTotals {
  requests: number
  tokens: number
  cost: number
  users: number
}

function sumUsage(items: UsageGroup[]): UsageTotals {
  return items.reduce<UsageTotals>(
    (acc, item) => ({
      requests: acc.requests + item.requests,
      tokens: acc.tokens + item.tokens,
      cost: acc.cost + item.cost,
      users: acc.users + item.users,
    }),
    { requests: 0, tokens: 0, cost: 0, users: 0 },
  )
}

function formatShare(part: number, total: number): string {
  if (total <= 0) return '0.0%'
  return `${((part / total) * 100).toFixed(1)}%`
}

export function UsagePage() {
  const [groupBy, setGroupBy] = useState<UsageGroupBy>('user')
  const [form, setForm] = useState(EMPTY_FORM)
  const [applied, setApplied] = useState(EMPTY_FORM)
  const [hint, setHint] = useState<string | null>(null)

  const filters = useMemo(
    () => ({
      groupBy,
      timeFrom: beijingInputToIso(applied.timeFrom),
      timeTo: beijingInputToIso(applied.timeTo),
    }),
    [groupBy, applied],
  )

  const query = useUsageSummaryQuery(filters)
  const items = query.data?.items ?? []
  const totals = useMemo(() => sumUsage(items), [items])
  const keyLabel = GROUP_OPTIONS.find((option) => option.value === groupBy)?.keyLabel ?? '分组'

  function apply() {
    if (form.timeFrom && form.timeTo && form.timeTo <= form.timeFrom) {
      setHint('结束时间需晚于开始时间')
      return
    }
    setHint(null)
    setApplied(form)
  }

  function reset() {
    setHint(null)
    setForm(EMPTY_FORM)
    setApplied(EMPTY_FORM)
  }

  return (
    <section className="space-y-3">
      <h1 className="text-lg font-semibold text-gray-900">AI 用量聚合</h1>

      <FilterBar onSubmit={apply} onReset={reset} busy={query.isFetching} hint={hint}>
        <FilterField label="时间起（北京时间）">
          <input
            type="datetime-local"
            className={inputClass}
            value={form.timeFrom}
            onChange={(event) => setForm((prev) => ({ ...prev, timeFrom: event.target.value }))}
          />
        </FilterField>
        <FilterField label="时间止（北京时间）">
          <input
            type="datetime-local"
            className={inputClass}
            value={form.timeTo}
            onChange={(event) => setForm((prev) => ({ ...prev, timeTo: event.target.value }))}
          />
        </FilterField>
      </FilterBar>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div
          role="group"
          aria-label="分组方式"
          className="inline-flex rounded-md border border-gray-300 bg-white p-0.5"
        >
          {GROUP_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              aria-pressed={groupBy === option.value}
              onClick={() => setGroupBy(option.value)}
              className={cn(
                'rounded px-3 py-1 text-sm',
                groupBy === option.value
                  ? 'bg-blue-600 font-medium text-white'
                  : 'text-gray-700 hover:bg-gray-50',
              )}
            >
              {option.label}
            </button>
          ))}
        </div>
        {query.isFetching ? <span className="text-xs text-gray-400">加载中…</span> : null}
      </div>

      <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
        <table className="w-full min-w-[880px] border-collapse text-left text-sm">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th scope="col" className="whitespace-nowrap border-b border-gray-200 px-3 py-2 font-medium">
                {keyLabel}
              </th>
              <th scope="col" className="whitespace-nowrap border-b border-gray-200 px-3 py-2 text-right font-medium">
                请求数
              </th>
              <th scope="col" className="whitespace-nowrap border-b border-gray-200 px-3 py-2 text-right font-medium">
                tokens
              </th>
              <th scope="col" className="whitespace-nowrap border-b border-gray-200 px-3 py-2 text-right font-medium">
                成本（微元）
              </th>
              <th scope="col" className="whitespace-nowrap border-b border-gray-200 px-3 py-2 text-right font-medium">
                用户数
              </th>
              <th scope="col" className="whitespace-nowrap border-b border-gray-200 px-3 py-2 font-medium">
                tokens 占比
              </th>
            </tr>
          </thead>
          <tbody>
            {query.error ? (
              <tr>
                <td colSpan={6} className="px-3 py-8 text-center">
                  <div className="space-y-2">
                    <div className="text-sm text-red-600">{describeError(query.error)}</div>
                    <button
                      type="button"
                      className={buttonSecondaryClass}
                      onClick={() => void query.refetch()}
                    >
                      重试
                    </button>
                  </div>
                </td>
              </tr>
            ) : query.isPending ? (
              <tr>
                <td colSpan={6} className="px-3 py-8 text-center text-sm text-gray-500">
                  加载中…
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-3 py-8 text-center text-sm text-gray-500">
                  该时间区间没有用量记录
                </td>
              </tr>
            ) : (
              items.map((item) => (
                <tr key={item.key} className="border-b border-gray-100 last:border-b-0">
                  <td className="max-w-[360px] px-3 py-2 align-top text-gray-800">
                    <KeyCell groupBy={groupBy} rowKey={item.key} />
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-right align-top font-mono tabular-nums text-gray-800">
                    {item.requests}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-right align-top font-mono tabular-nums text-gray-800">
                    {item.tokens}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-right align-top font-mono tabular-nums text-gray-800">
                    {item.cost}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-right align-top font-mono tabular-nums text-gray-800">
                    {item.users}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 align-top">
                    <div className="flex items-center gap-2">
                      <div className="h-2 w-24 shrink-0 rounded bg-gray-100">
                        <div
                          className="h-2 rounded bg-blue-500"
                          style={{
                            width: totals.tokens > 0
                              ? `${Math.min(100, (item.tokens / totals.tokens) * 100)}%`
                              : '0%',
                          }}
                        />
                      </div>
                      <span className="w-14 text-right font-mono text-xs tabular-nums text-gray-500">
                        {formatShare(item.tokens, totals.tokens)}
                      </span>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
          {items.length > 0 ? (
            <tfoot className="bg-gray-50 font-medium text-gray-900">
              <tr>
                <td className="whitespace-nowrap border-t border-gray-200 px-3 py-2">合计</td>
                <td className="whitespace-nowrap border-t border-gray-200 px-3 py-2 text-right font-mono tabular-nums">
                  {totals.requests}
                </td>
                <td className="whitespace-nowrap border-t border-gray-200 px-3 py-2 text-right font-mono tabular-nums">
                  {totals.tokens}
                </td>
                <td className="whitespace-nowrap border-t border-gray-200 px-3 py-2 text-right font-mono tabular-nums">
                  {totals.cost}
                </td>
                <td className="whitespace-nowrap border-t border-gray-200 px-3 py-2 text-right font-mono tabular-nums">
                  {groupBy === 'user' ? totals.users : '—'}
                </td>
                <td className="whitespace-nowrap border-t border-gray-200 px-3 py-2 font-mono text-xs tabular-nums text-gray-500">
                  {totals.tokens > 0 ? '100.0%' : '0.0%'}
                </td>
              </tr>
            </tfoot>
          ) : null}
        </table>
      </div>

      <p className="text-xs text-gray-500">
        成本单位：微元（1e-6 元），取 ai_usage.cost 后端原值，前端不换算。按天分组键由服务端
        date(created_at) 生成，原样展示、不改写时区；时间筛选以北京时间输入、带
        <code className="mx-1 font-mono">+08:00</code>传给后端。用户数合计仅按用户分组时展示（其余分组下各组用户可能重叠）。
      </p>
    </section>
  )
}

/** 分组键渲染：用户 ID 用等宽截断，模型/日期原样等宽展示。 */
function KeyCell({ groupBy, rowKey }: { groupBy: UsageGroupBy; rowKey: string }) {
  if (groupBy === 'user') return <MonoId value={rowKey} />
  return (
    <span className="block truncate font-mono text-xs text-gray-700" title={rowKey}>
      {rowKey}
    </span>
  )
}
