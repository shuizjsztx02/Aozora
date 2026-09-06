/**
 * SyncBadge — 同步状态徽章。
 */

import { useSyncStatus } from '../platform/store/sync-store'
import type { SyncStateValue } from '../platform/bridge/bridge-types'

const stateIcons: Record<SyncStateValue, string> = {
  idle: '✓',
  pushing: '↑',
  pulling: '↓',
  rebuilding: '↻',
  'auth-lost': '!',
  error: '✕',
}

const stateColors: Record<SyncStateValue, string> = {
  idle: 'var(--income)',
  pushing: 'var(--warn)',
  pulling: 'var(--accent)',
  rebuilding: 'var(--accent)',
  'auth-lost': 'var(--expense)',
  error: 'var(--expense)',
}

const stateLabels: Record<SyncStateValue, string> = {
  idle: '已同步',
  pushing: '上传中',
  pulling: '下载中',
  rebuilding: '重建中',
  'auth-lost': '登录失效',
  error: '同步失败',
}

export function SyncBadge() {
  const status = useSyncStatus()
  const icon = stateIcons[status.state]
  const color = stateColors[status.state]

  if (status.state === 'idle' && status.pendingCount === 0) {
    return (
      <span className="text-xs text-ink3 flex items-center gap-1" data-testid="sync-badge">
        <span style={{ color }}>{icon}</span>
        已同步
      </span>
    )
  }

  return (
    <span className="text-xs flex items-center gap-1" data-testid="sync-badge" style={{ color }}>
      <span className={status.state !== 'idle' ? 'animate-pulse' : ''}>{icon}</span>
      {status.pendingCount > 0 ? `${status.pendingCount} 条待同步` : stateLabels[status.state]}
    </span>
  )
}
