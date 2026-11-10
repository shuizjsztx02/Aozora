import { useEffect, type ReactNode } from 'react'
import { cn } from '../lib/cn'

interface DetailDrawerProps {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
}

/** 右侧详情抽屉：Esc 关闭，窄屏占满宽度。 */
export function DetailDrawer({ open, title, onClose, children }: DetailDrawerProps) {
  useEffect(() => {
    if (!open) return
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button
        type="button"
        aria-label="关闭详情"
        className="absolute inset-0 bg-black/30"
        onClick={onClose}
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="relative z-50 h-full w-full max-w-lg overflow-y-auto bg-white shadow-xl"
      >
        <header className="sticky top-0 flex items-center justify-between gap-2 border-b border-gray-200 bg-white px-4 py-3">
          <h2 className="text-base font-semibold text-gray-900">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md px-2 py-1 text-sm text-gray-500 hover:bg-gray-100 hover:text-gray-700"
          >
            关闭
          </button>
        </header>
        <div className="px-4 py-3">{children}</div>
      </aside>
    </div>
  )
}

/** 抽屉 / 详情页共用的键值行。 */
export function DetailRow({
  label,
  value,
  mono = false,
}: {
  label: string
  value: ReactNode
  mono?: boolean
}) {
  return (
    <div className="grid grid-cols-[7rem_1fr] gap-2 border-b border-gray-100 py-1.5 text-sm last:border-b-0">
      <span className="text-gray-500">{label}</span>
      <span className={cn('break-all text-gray-800', mono && 'font-mono text-xs')}>
        {value ?? '—'}
      </span>
    </div>
  )
}
