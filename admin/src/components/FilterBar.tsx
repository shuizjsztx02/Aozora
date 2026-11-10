import type { FormEvent, ReactNode } from 'react'
import { buttonPrimaryClass, buttonSecondaryClass } from '../lib/ui'

interface FilterBarProps {
  children: ReactNode
  onSubmit: () => void
  onReset: () => void
  busy?: boolean
  /** 表单级提示（如关键词过短），在按钮行下方展示。 */
  hint?: string | null
}

/** 多列 grid 筛选栏：`sm` 2 列、`lg` 4 列，窄屏单列（16px 页面边距由 Shell 提供）。 */
export function FilterBar({ children, onSubmit, onReset, busy = false, hint }: FilterBarProps) {
  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    onSubmit()
  }

  return (
    <form onSubmit={handleSubmit} className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">{children}</div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="submit" className={buttonPrimaryClass} disabled={busy}>
          {busy ? '查询中…' : '查询'}
        </button>
        <button type="button" className={buttonSecondaryClass} onClick={onReset}>
          重置
        </button>
        {hint ? <span className="text-sm text-red-600">{hint}</span> : null}
      </div>
    </form>
  )
}

export function FilterField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1">
      <span className="text-xs font-medium text-gray-600">{label}</span>
      {children}
    </label>
  )
}
