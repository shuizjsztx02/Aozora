import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router'
import { QUERY_MIN_CHARS, type ConversationFilters } from '../api/admin'
import { useUserConversationsQuery, useUserQuery } from '../api/hooks'
import { DataTable } from '../components/DataTable'
import { DetailRow } from '../components/DetailDrawer'
import { FilterBar, FilterField } from '../components/FilterBar'
import { Pagination } from '../components/Pagination'
import { StatusBadge } from '../components/StatusBadge'
import { conversationColumns } from '../components/conversationColumns'
import { describeError } from '../lib/errors'
import { useKeysetPager } from '../lib/pager'
import { beijingInputToIso, formatBeijing } from '../lib/time'
import { inputClass, linkClass } from '../lib/ui'

interface ConversationFilterForm {
  q: string
  timeFrom: string
  timeTo: string
  includeArchived: boolean
}

const EMPTY_FORM: ConversationFilterForm = {
  q: '',
  timeFrom: '',
  timeTo: '',
  includeArchived: false,
}

/** 用户详情 + 该用户的会话列表（数据源：`/admin/users/{id}` 与 `.../conversations`）。 */
export function UserDetailPage() {
  const { userId } = useParams<{ userId: string }>()
  const userQuery = useUserQuery(userId ?? null)

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
      time_from: beijingInputToIso(applied.timeFrom),
      time_to: beijingInputToIso(applied.timeTo),
      include_archived: applied.includeArchived,
    }),
    [applied],
  )

  const conversationsQuery = useUserConversationsQuery(userId ?? null, filters, pager.cursor)
  const items = conversationsQuery.data?.items ?? []

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold text-gray-900">用户详情</h1>
        <Link to="/users" className={linkClass}>
          ← 返回用户检索
        </Link>
      </div>

      <div className="rounded-lg border border-gray-200 bg-white p-4">
        {userQuery.isPending ? (
          <p className="text-sm text-gray-500">加载中…</p>
        ) : userQuery.error ? (
          <p className="text-sm text-red-600">{describeError(userQuery.error)}</p>
        ) : userQuery.data ? (
          <div className="grid grid-cols-1 gap-x-8 lg:grid-cols-2">
            <div>
              <DetailRow label="用户 ID" value={userQuery.data.id} mono />
              <DetailRow label="手机号" value={userQuery.data.phone} />
              <DetailRow label="昵称" value={userQuery.data.nickname} />
              <DetailRow label="状态" value={<StatusBadge status={userQuery.data.status} />} />
              <DetailRow label="AI 日配额覆盖" value={userQuery.data.ai_daily_quota_override} />
              <DetailRow label="设备数" value={userQuery.data.device_count} />
            </div>
            <div>
              <DetailRow label="会话数" value={userQuery.data.conversation_count} />
              <DetailRow label="最近活动" value={formatBeijing(userQuery.data.last_activity_at)} />
              <DetailRow label="注册时间" value={formatBeijing(userQuery.data.created_at)} />
              <DetailRow label="更新时间" value={formatBeijing(userQuery.data.updated_at)} />
              <DetailRow label="暂停时间" value={formatBeijing(userQuery.data.admin_suspended_at)} />
              <DetailRow label="暂停原因" value={userQuery.data.admin_suspended_reason} />
            </div>
          </div>
        ) : null}
      </div>

      <h2 className="text-base font-semibold text-gray-900">该用户的会话</h2>

      <FilterBar
        onSubmit={apply}
        onReset={reset}
        busy={conversationsQuery.isFetching}
        hint={hint}
      >
        <FilterField label={`关键词（≥ ${QUERY_MIN_CHARS} 字符）`}>
          <input
            className={inputClass}
            value={form.q}
            onChange={(event) => update('q', event.target.value)}
            placeholder="用户输入或助手回复中的关键词"
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
        emptyMessage={userQuery.error ? '用户不存在' : '该用户没有符合条件的会话'}
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
