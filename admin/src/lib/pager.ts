import { useCallback, useState } from 'react'

/**
 * keyset 分页器（架构 §3.3「禁止 offset」）。
 *
 * 后端只给 `next_cursor`，没有「上一页」游标；这里保留一个已消费游标栈：
 * 栈顶是当前页的游标，`goPrev` 弹栈即回到上一页。
 */
export interface KeysetPager {
  /** 当前页请求使用的游标；第一页为 null。 */
  cursor: string | null
  /** 0 起的页序号（仅用于展示，不代表总页数）。 */
  pageIndex: number
  hasPrev: boolean
  goNext: (nextCursor: string) => void
  goPrev: () => void
  reset: () => void
}

export function useKeysetPager(): KeysetPager {
  const [stack, setStack] = useState<string[]>([])

  const goNext = useCallback((nextCursor: string) => {
    setStack((prev) => [...prev, nextCursor])
  }, [])

  const goPrev = useCallback(() => {
    setStack((prev) => prev.slice(0, -1))
  }, [])

  const reset = useCallback(() => {
    setStack([])
  }, [])

  return {
    cursor: stack.length > 0 ? stack[stack.length - 1] : null,
    pageIndex: stack.length,
    hasPrev: stack.length > 0,
    goNext,
    goPrev,
    reset,
  }
}
