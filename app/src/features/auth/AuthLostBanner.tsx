/**
 * AuthLostView — 同步认证丢失提示。
 *
 * 当 SyncEngine 检测到 token 失效/被吊销时，通过 window.__aozoraSyncEvent__
 * 推送 `state: 'auth-lost'`，此组件在任意页面展示提醒并引导用户重新注册。
 */

import { useSyncStatus } from '../../platform/store/sync-store'
import { useNavigate } from 'react-router'

export function AuthLostBanner() {
  const status = useSyncStatus()
  const navigate = useNavigate()

  if (status.state !== 'auth-lost') return null

  return (
    <div
      className="fixed top-0 left-0 right-0 z-50 glass-card p-4 flex items-center justify-between gap-3"
      style={{ boxShadow: '0 2px 12px rgba(0,0,0,0.15)' }}
      role="alert"
      aria-live="assertive"
    >
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium" style={{ color: 'var(--expense)' }}>认证已失效</div>
        <div className="text-xs text-ink3 mt-0.5 truncate">{status.lastError ?? '请重新登录以恢复同步'}</div>
      </div>
      <button
        onClick={() => navigate('/login')}
        className="shrink-0 py-1.5 px-4 rounded-lg text-sm font-medium text-white whitespace-nowrap"
        style={{ background: 'var(--accent)' }}
      >
        重新登录
      </button>
    </div>
  )
}
