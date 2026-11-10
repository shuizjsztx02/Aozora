import { buttonSecondaryClass } from '../lib/ui'

interface PaginationProps {
  /** 0 起的页序号。 */
  pageIndex: number
  /** 本页条数。keyset 分页没有总数。 */
  rowCount: number
  hasPrev: boolean
  hasNext: boolean
  isLoading?: boolean
  onPrev: () => void
  onNext: () => void
}

/** 游标分页：上一页走本地游标栈，下一页用后端 `next_cursor`。 */
export function Pagination({
  pageIndex,
  rowCount,
  hasPrev,
  hasNext,
  isLoading = false,
  onPrev,
  onNext,
}: PaginationProps) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="text-sm text-gray-500">
        第 {pageIndex + 1} 页 · 本页 {rowCount} 条
        <span className="ml-2 text-xs text-gray-400">（游标分页，无总数）</span>
      </span>
      <div className="flex gap-2">
        <button
          type="button"
          className={buttonSecondaryClass}
          onClick={onPrev}
          disabled={!hasPrev || isLoading}
        >
          上一页
        </button>
        <button
          type="button"
          className={buttonSecondaryClass}
          onClick={onNext}
          disabled={!hasNext || isLoading}
        >
          下一页
        </button>
      </div>
    </div>
  )
}
