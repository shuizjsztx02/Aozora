import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table'
import { useState } from 'react'
import { cn } from '../lib/cn'
import { buttonSecondaryClass } from '../lib/ui'

export interface DataTableProps<TData> {
  // 单元格渲染按列取不同值类型，这里保留 any（对外 API 仍受 TData 约束）。
  columns: ColumnDef<TData, any>[]
  data: TData[]
  rowKey: (row: TData) => string
  isLoading?: boolean
  error?: string | null
  emptyMessage?: string
  onRetry?: () => void
  onRowClick?: (row: TData) => void
  /** 固定在左侧的列 id（窄屏横向滚动时保持标识列可见）。 */
  pinnedColumns?: string[]
  /** 表格最小宽度——宽表格在容器内横向滚动，页面本身不横向滚动。 */
  minWidthClass?: string
}

/** 通用宽表格：排序 / 列固定 / 空态 / 加载态 / 失败态。 */
export function DataTable<TData>({
  columns,
  data,
  rowKey,
  isLoading = false,
  error = null,
  emptyMessage = '无数据',
  onRetry,
  onRowClick,
  pinnedColumns = [],
  minWidthClass = 'min-w-[720px]',
}: DataTableProps<TData>) {
  const [sorting, setSorting] = useState<SortingState>([])

  const table = useReactTable({
    data,
    columns,
    state: { sorting, columnPinning: { left: pinnedColumns } },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  const columnCount = table.getAllLeafColumns().length

  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
      <table className={cn('w-full border-collapse text-left text-sm', minWidthClass)}>
        <thead className="bg-gray-50 text-gray-600">
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map((header) => {
                const pinned = header.column.getIsPinned() === 'left'
                const canSort = header.column.getCanSort()
                return (
                  <th
                    key={header.id}
                    scope="col"
                    className={cn(
                      'whitespace-nowrap border-b border-gray-200 px-3 py-2 font-medium',
                      pinned && 'sticky left-0 z-10 bg-gray-50',
                    )}
                  >
                    {header.isPlaceholder ? null : canSort ? (
                      <button
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        className="inline-flex items-center gap-1 hover:text-gray-900"
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        <span aria-hidden="true" className="text-xs text-gray-400">
                          {sortIcon(header.column.getIsSorted())}
                        </span>
                      </button>
                    ) : (
                      flexRender(header.column.columnDef.header, header.getContext())
                    )}
                  </th>
                )
              })}
            </tr>
          ))}
        </thead>
        <tbody className={cn(isLoading && data.length > 0 && 'opacity-60')}>
          {error ? (
            <tr>
              <td colSpan={columnCount} className="px-3 py-8 text-center">
                <div className="space-y-2">
                  <div className="text-sm text-red-600">{error}</div>
                  {onRetry ? (
                    <button type="button" className={buttonSecondaryClass} onClick={onRetry}>
                      重试
                    </button>
                  ) : null}
                </div>
              </td>
            </tr>
          ) : isLoading && data.length === 0 ? (
            <tr>
              <td colSpan={columnCount} className="px-3 py-8 text-center text-sm text-gray-500">
                加载中…
              </td>
            </tr>
          ) : data.length === 0 ? (
            <tr>
              <td colSpan={columnCount} className="px-3 py-8 text-center text-sm text-gray-500">
                {emptyMessage}
              </td>
            </tr>
          ) : (
            table.getRowModel().rows.map((row) => (
              <tr
                key={rowKey(row.original)}
                className={cn(
                  'group border-b border-gray-100 last:border-b-0',
                  onRowClick && 'cursor-pointer hover:bg-gray-50',
                )}
                onClick={onRowClick ? () => onRowClick(row.original) : undefined}
              >
                {row.getVisibleCells().map((cell) => {
                  const pinned = cell.column.getIsPinned() === 'left'
                  return (
                    <td
                      key={cell.id}
                      className={cn(
                        'px-3 py-2 align-top text-gray-800',
                        pinned && 'sticky left-0 z-10 bg-white group-hover:bg-gray-50',
                      )}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  )
                })}
              </tr>
            ))
          )}
        </tbody>
      </table>
      {isLoading && data.length > 0 ? (
        <div className="border-t border-gray-100 px-3 py-1 text-right text-xs text-gray-400">
          加载中…
        </div>
      ) : null}
    </div>
  )
}

function sortIcon(sorted: false | 'asc' | 'desc'): string {
  if (sorted === 'asc') return '↑'
  if (sorted === 'desc') return '↓'
  return '↕'
}
