import { useMemo, useState } from 'react'
import { QUERY_MIN_CHARS, type ConversationFilters } from '../api/admin'
import { useConversationsQuery } from '../api/hooks'
import { DataTable } from '../components/DataTable'
import { ExportButton } from '../components/ExportButton'
import { FilterBar, FilterField } from '../components/FilterBar'
import { Pagination } from '../components/Pagination'
import { conversationColumns } from '../components/conversationColumns'
import { describeError } from '../lib/errors'
import { useKeysetPager } from '../lib/pager'
import { beijingInputToIso } from '../lib/time'
import { inputClass } from '../lib/ui'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

interface ConversationFilterForm {
  q: string
  userId: string
  timeFrom: string
  timeTo: string
  includeArchived: boolean
}

const EMPTY_FORM: ConversationFilterForm = {
  q: '',
  userId: '',
  timeFrom: '',
  timeTo: '',
  includeArchived: false,
}

export function ConversationsPage() {
  const [form, setForm] = useState<ConversationFilterForm>(EMPTY_FORM)
  const [applied, setApplied] = useState<ConversationFilterForm>(EMPTY_FORM)
  const [hint, setHint] = useState<string | null>(null)
  const pager = useKeysetPager()

  function update<K extends keyof ConversationFilterForm>(
    key: K,
    value: ConversationFilterForm[K],
  ) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  function apply() {
    const q = form.q.trim()
    if (q && q.length < QUERY_MIN_CHARS) {
      setHint(`关键词至少 ${QUERY_MIN_CHARS} 个字符`)
      return
    }
    const userId = form.userId.trim()
    if (userId && !UUID_RE.test(userId)) {
      setHint('用户 ID 需为完整 UUID')
      return
    }
    setHint(null)
    setApplied(form)
    pager.reset()
  }

  function reset() {
    setHint(null)
    setForm(EMPTY_FORM)
    setApplied(EMPTY_FORM)
    pager.reset()
  }

  const filters = useMemo<ConversationFilters>(
    () => ({
      q: applied.q.trim() || undefined,
      user_id: applied.userId.trim() || undefined,
      time_from: beijingInputToIso(applied.timeFrom),
      time_to: beijingInputToIso(applied.timeTo),
      include_archived: applied.includeArchived,
    }),
    [applied],
  )

  const conversationsQuery = useConversationsQuery(filters, pager.cursor)
  const items = conversationsQuery.data?.items ?? []

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold text-gray-900">会话检索</h1>
        {/* T-M8-09：导出当前已应用的筛选结果（打码版；明文请在导出页选）。 */}
        <ExportButton
          label="导出当前筛选"
          request={() => ({
            scope: 'filtered',
            format: 'masked',
            content: 'events',
            q: filters.q,
            user_id: filters.user_id,
            time_from: filters.time_from,
            time_to: filters.time_to,
            include_archived: filters.include_archived,
          })}
        />
      </div>

      <FilterBar onSubmit={apply} onReset={reset} busy={conversationsQuery.isFetching} hint={hint}>
        <FilterField label={`关键词（≥ ${QUERY_MIN_CHARS} 字符）`}>
          <input
            className={inputClass}
            value={form.q}
            onChange={(event) => update('q', event.target.value)}
            placeholder="用户输入或助手回复中的关键词"
          />
        </FilterField>
        <FilterField label="用户 ID（可选）">
          <input
            className={inputClass}
            value={form.userId}
            onChange={(event) => update('userId', event.target.value)}
            placeholder="完整 UUID，留空为全局检索"
          />
        </FilterField>
        <FilterField label="最后活跃起（北京时间）">
          <input
            type="datetime-local"
            className={inputClass}
            value={form.timeFrom}
            onChange={(event) => update('timeFrom', event.target.value)}
          />
        </FilterField>
        <FilterField label="最后活跃止（北京时间）">
          <input
            type="datetime-local"
            className={inputClass}
            value={form.timeTo}
            onChange={(event) => update('timeTo', event.target.value)}
          />
        </FilterField>
        <label className="flex items-center gap-2 text-sm text-gray-700">
          <input
            type="checkbox"
            checked={form.includeArchived}
            onChange={(event) => update('includeArchived', event.target.checked)}
          />
          包含已归档会话
        </label>
      </FilterBar>

      <DataTable
        columns={conversationColumns}
        data={items}
        rowKey={(row) => row.id}
        isLoading={conversationsQuery.isFetching}
        error={conversationsQuery.error ? describeError(conversationsQuery.error) : null}
        onRetry={() => void conversationsQuery.refetch()}
        pinnedColumns={['id']}
        minWidthClass="min-w-[960px]"
        emptyMessage="没有符合条件的会话"
      />

      <Pagination
        pageIndex={pager.pageIndex}
        rowCount={items.length}
        hasPrev={pager.hasPrev}
        hasNext={Boolean(conversationsQuery.data?.next_cursor)}
        isLoading={conversationsQuery.isFetching}
        onPrev={pager.goPrev}
        onNext={() => {
          const next = conversationsQuery.data?.next_cursor
          if (next) pager.goNext(next)
        }}
      />
    </section>
  )
}
