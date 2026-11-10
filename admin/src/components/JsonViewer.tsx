/** 折叠式 JSON 查看器——工具参数 / 未知事件载荷的兜底展示。 */

import { cn } from '../lib/cn'

interface JsonViewerProps {
  value: unknown
  label?: string
  defaultOpen?: boolean
  className?: string
}

export function JsonViewer({
  value,
  label = '原始数据',
  defaultOpen = false,
  className,
}: JsonViewerProps) {
  return (
    <details
      open={defaultOpen}
      className={cn('rounded-md border border-gray-200 bg-gray-50', className)}
    >
      <summary className="cursor-pointer select-none px-2 py-1 text-xs text-gray-500 hover:text-gray-700">
        {label}
      </summary>
      <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all border-t border-gray-200 px-2 py-1.5 font-mono text-xs leading-relaxed text-gray-700">
        {stringify(value)}
      </pre>
    </details>
  )
}

function stringify(value: unknown): string {
  if (value === undefined) return '—'
  try {
    return JSON.stringify(value, null, 2) ?? String(value)
  } catch {
    return String(value)
  }
}
