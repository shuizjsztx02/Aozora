import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import type { ColumnDef } from '@tanstack/react-table'
import { useUserQuery, useUsersQuery } from '../api/hooks'
import type { AdminUserItem, UserStatus } from '../api/types'
import { type UserFilters } from '../api/admin'
import { DataTable } from '../components/DataTable'
import { DetailDrawer, DetailRow } from '../components/DetailDrawer'
import { FilterBar, FilterField } from '../components/FilterBar'
import { MonoId } from '../components/MonoId'
import { Pagination } from '../components/Pagination'
import { StatusBadge, USER_STATUS_OPTIONS } from '../components/StatusBadge'
import { describeError } from '../lib/errors'
import { useKeysetPager } from '../lib/pager'
import { beijingInputToIso, formatBeijing } from '../lib/time'
import { inputClass, linkClass } from '../lib/ui'

interface UserFilterForm {
  q: string
  status: '' | UserStatus
  createdFrom: string
  createdTo: string
}

const EMPTY_FORM: UserFilterForm = { q: '', status: '', createdFrom: '', createdTo: '' }

export function UsersPage() {
  const [form, setForm] = useState<UserFilterForm>(EMPTY_FORM)
  const [applied, setApplied] = useState<UserFilterForm>(EMPTY_FORM)
  const [drawerUserId, setDrawerUserId] = useState<string | null>(null)
  const pager = useKeysetPager()

  function update<K extends keyof UserFilterForm>(key: K, value: UserFilterForm[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  const filters = useMemo<UserFilters>(
    () => ({
      q: applied.q.trim() || undefined,
      status: applied.status || undefined,
      created_from: beijingInputToIso(applied.createdFrom),
      created_to: beijingInputToIso(applied.createdTo),
    }),
    [applied],
  )

  const usersQuery = useUsersQuery(filters, pager.cursor)
  const detailQuery = useUserQuery(drawerUserId)

  const columns = useMemo<ColumnDef<AdminUserItem, unknown>[]>(
    () => [
      {
        id: 'id',
        header: '用户 ID',
        accessorKey: 'id',
        enableSorting: false,
        cell: ({ getValue }) => <MonoId value={String(getValue())} />,
      },
      {
        id: 'phone',
        header: '手机号',
        accessorKey: 'phone',
        cell: ({ getValue }) => (getValue() as string | null) ?? '—',
      },
      {
        id: 'nickname',
        header: '昵称',
        accessorKey: 'nickname',
        cell: ({ getValue }) => (getValue() as string | null) ?? '—',
      },
      {
        id: 'status',
        header: '状态',
        accessorKey: 'status',
        enableSorting: false,
        cell: ({ getValue }) => <StatusBadge status={String(getValue())} />,
      },
      {
        id: 'quota',
        header: 'AI 日配额覆盖',
        accessorKey: 'ai_daily_quota_override',
        enableSorting: false,
        cell: ({ getValue }) => (getValue() as number | null) ?? '—',
      },
      {
        id: 'suspended',
        header: '暂停时间',
        accessorKey: 'admin_suspended_at',
        cell: ({ getValue }) => formatBeijing(getValue() as string | null),
      },
      {
        id: 'created_at',
        header: '注册时间',
        accessorKey: 'created_at',
        cell: ({ getValue }) => formatBeijing(String(getValue())),
      },
      {
        id: 'actions',
        header: '操作',
        enableSorting: false,
        cell: ({ row }) => (
          <Link
            to={`/users/${row.original.id}`}
            className={linkClass}
            onClick={(event) => event.stopPropagation()}
          >
            详情
          </Link>
        ),
      },
    ],
    [],
  )

  const items = usersQuery.data?.items ?? []

  return (
    <section className="space-y-3">
      <h1 className="text-lg font-semibold text-gray-900">用户检索</h1>

      <FilterBar
        onSubmit={() => {
          setApplied(form)
          pager.reset()
        }}
        onReset={() => {
          setForm(EMPTY_FORM)
          setApplied(EMPTY_FORM)
          pager.reset()
        }}
        busy={usersQuery.isFetching}
      >
        <FilterField label="手机号 / 昵称 / 用户 ID">
          <input
            className={inputClass}
            value={form.q}
            onChange={(event) => update('q', event.target.value)}
            placeholder="关键词"
          />
        </FilterField>
        <FilterField label="状态">
          <select
            className={inputClass}
            value={form.status}
            onChange={(event) =>
              update('status', event.target.value as UserFilterForm['status'])
            }
          >
            <option value="">全部</option>
            {USER_STATUS_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </FilterField>
        <FilterField label="注册时间起（北京时间）">
          <input
            type="datetime-local"
            className={inputClass}
            value={form.createdFrom}
            onChange={(event) => update('createdFrom', event.target.value)}
          />
        </FilterField>
        <FilterField label="注册时间止（北京时间）">
          <input
            type="datetime-local"
            className={inputClass}
            value={form.createdTo}
            onChange={(event) => update('createdTo', event.target.value)}
          />
        </FilterField>
      </FilterBar>

      <DataTable
        columns={columns}
        data={items}
        rowKey={(row) => row.id}
        isLoading={usersQuery.isFetching}
        error={usersQuery.error ? describeError(usersQuery.error) : null}
        onRetry={() => void usersQuery.refetch()}
        onRowClick={(row) => setDrawerUserId(row.id)}
        pinnedColumns={['id']}
        minWidthClass="min-w-[960px]"
        emptyMessage="没有符合条件的用户"
      />

      <Pagination
        pageIndex={pager.pageIndex}
        rowCount={items.length}
        hasPrev={pager.hasPrev}
        hasNext={Boolean(usersQuery.data?.next_cursor)}
        isLoading={usersQuery.isFetching}
        onPrev={pager.goPrev}
        onNext={() => {
          const next = usersQuery.data?.next_cursor
          if (next) pager.goNext(next)
        }}
      />

      <DetailDrawer
        open={Boolean(drawerUserId)}
        title="用户详情"
        onClose={() => setDrawerUserId(null)}
      >
        {detailQuery.isPending ? (
          <p className="text-sm text-gray-500">加载中…</p>
        ) : detailQuery.error ? (
          <p className="text-sm text-red-600">{describeError(detailQuery.error)}</p>
        ) : detailQuery.data ? (
          <div>
            <DetailRow label="用户 ID" value={detailQuery.data.id} mono />
            <DetailRow label="手机号" value={detailQuery.data.phone} />
            <DetailRow label="昵称" value={detailQuery.data.nickname} />
            <DetailRow
              label="状态"
              value={<StatusBadge status={detailQuery.data.status} />}
            />
            <DetailRow
              label="AI 日配额覆盖"
              value={detailQuery.data.ai_daily_quota_override}
            />
            <DetailRow label="设备数" value={detailQuery.data.device_count} />
            <DetailRow label="会话数" value={detailQuery.data.conversation_count} />
            <DetailRow
              label="最近活动"
              value={formatBeijing(detailQuery.data.last_activity_at)}
            />
            <DetailRow
              label="注册时间"
              value={formatBeijing(detailQuery.data.created_at)}
            />
            <DetailRow
              label="更新时间"
              value={formatBeijing(detailQuery.data.updated_at)}
            />
            <DetailRow
              label="暂停时间"
              value={formatBeijing(detailQuery.data.admin_suspended_at)}
            />
            <DetailRow
              label="暂停原因"
              value={detailQuery.data.admin_suspended_reason}
            />
            <Link
              to={`/users/${detailQuery.data.id}`}
              className={`${linkClass} mt-3 inline-block`}
              onClick={() => setDrawerUserId(null)}
            >
              打开完整详情页（含会话列表）
            </Link>
          </div>
        ) : null}
      </DetailDrawer>
    </section>
  )
}
