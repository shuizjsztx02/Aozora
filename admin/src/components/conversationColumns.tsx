import type { ColumnDef } from '@tanstack/react-table'
import { Link } from 'react-router'
import type { AdminConversationItem } from '../api/types'
import { formatBeijing } from '../lib/time'
import { MonoId } from './MonoId'

/** 会话表格列——全局检索页与用户详情页共用。 */
export const conversationColumns: ColumnDef<AdminConversationItem, unknown>[] = [
  {
    id: 'id',
    header: '会话 ID',
    accessorKey: 'id',
    enableSorting: false,
    cell: ({ getValue }) => <MonoId value={String(getValue())} />,
  },
  {
    id: 'user_id',
    header: '用户 ID',
    accessorKey: 'user_id',
    enableSorting: false,
    cell: ({ getValue }) => <MonoId value={String(getValue())} />,
  },
  {
    id: 'title',
    header: '标题',
    accessorKey: 'title',
    cell: ({ getValue }) => (
      <span className="block max-w-[320px] truncate" title={String(getValue())}>
        {String(getValue()) || '（无标题）'}
      </span>
    ),
  },
  {
    id: 'last_active_at',
    header: '最后活跃',
    accessorKey: 'last_active_at',
    cell: ({ getValue }) => formatBeijing(getValue() as string | null),
  },
  {
    id: 'created_at',
    header: '创建时间',
    accessorKey: 'created_at',
    cell: ({ getValue }) => formatBeijing(String(getValue())),
  },
  {
    id: 'archived_at',
    header: '归档',
    accessorKey: 'archived_at',
    cell: ({ getValue }) => {
      const archivedAt = getValue() as string | null
      return archivedAt ? (
        <span className="text-xs text-amber-700">已归档</span>
      ) : (
        <span className="text-xs text-gray-400">否</span>
      )
    },
  },
  {
    id: 'actions',
    header: '操作',
    enableSorting: false,
    cell: ({ row }) => (
      <Link
        to={`/replay/${row.original.id}`}
        className="text-blue-600 hover:underline"
      >
        回放
      </Link>
    ),
  },
]
