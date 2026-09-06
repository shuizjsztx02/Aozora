/**
 * 通用状态视图组件 — 空态 / 加载态 / 错误态 / 待同步态。
 */

interface EmptyStateProps {
  icon?: string
  title: string
  description?: string
  action?: React.ReactNode
}

export function EmptyState({ icon = '📭', title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-12 px-6 text-center" data-testid="empty-state">
      <div className="text-5xl mb-4">{icon}</div>
      <h3 className="text-base font-medium mb-1" style={{ color: 'var(--ink)' }}>{title}</h3>
      {description && <p className="text-sm text-ink2 mb-4">{description}</p>}
      {action}
    </div>
  )
}

export function LoadingState() {
  return (
    <div className="flex items-center justify-center py-12" data-testid="loading-state">
      <div
        className="w-8 h-8 rounded-full border-2 border-t-transparent animate-spin"
        style={{ borderColor: 'var(--accent)', borderTopColor: 'transparent' }}
      />
    </div>
  )
}

interface ErrorStateProps {
  message?: string
  onRetry?: () => void
}

export function ErrorState({ message = '加载失败', onRetry }: ErrorStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-12 px-6 text-center" data-testid="error-state">
      <div className="text-4xl mb-3">⚠️</div>
      <p className="text-sm text-ink2 mb-3">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="px-4 py-2 rounded-lg text-sm font-medium"
          style={{ background: 'var(--accent)', color: 'white' }}
        >
          重试
        </button>
      )}
    </div>
  )
}

interface PendingSyncStateProps {
  count: number
  lastError?: string | null
}

export function PendingSyncState({ count, lastError }: PendingSyncStateProps) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 rounded-full text-xs" data-testid="pending-sync-state"
      style={{ background: 'var(--glass)', color: 'var(--ink2)' }}>
      <span className="inline-block w-1.5 h-1.5 rounded-full animate-pulse"
        style={{ background: count > 0 ? 'var(--warn)' : 'var(--income)' }} />
      <span>{count > 0 ? `${count} 条待同步` : '已同步'}</span>
      {lastError && <span className="text-expense">· 有误</span>}
    </div>
  )
}
