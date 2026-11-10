/**
 * T-M8-09：列表页 / 回放页的「导出当前视图」按钮。
 *
 * 用户决策 14：**不做二次确认**——点击即导出；服务端每次写一条
 * `admin_audit_log(action="export")`。
 */

import { useState } from 'react'
import { downloadExport, formatBytes, type ExportRequest } from '../api/export'
import { useAuth } from '../auth/AuthContext'
import { cn } from '../lib/cn'
import { describeError } from '../lib/errors'
import { buttonSecondaryClass } from '../lib/ui'

interface ExportButtonProps {
  label: string
  /** 点击时才取值——调用方拿的是当前已应用的筛选。 */
  request: () => ExportRequest
  className?: string
  disabled?: boolean
}

export function ExportButton({ label, request, className, disabled }: ExportButtonProps) {
  const { session } = useAuth()
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function run() {
    if (!session) {
      setError('登录已失效，请重新登录')
      return
    }
    setBusy(true)
    setMessage(null)
    setError(null)
    try {
      const result = await downloadExport(request(), session.accessToken, {
        onProgress: (bytes) => setMessage(`已接收 ${formatBytes(bytes)}…`),
      })
      setMessage(`已下载 ${result.filename}（${formatBytes(result.bytes)}）`)
    } catch (err) {
      setMessage(null)
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={cn('flex flex-wrap items-center gap-2', className)}>
      <button
        type="button"
        className={buttonSecondaryClass}
        onClick={() => void run()}
        disabled={busy || disabled}
      >
        {busy ? '导出中…' : label}
      </button>
      {message ? <span className="text-xs text-gray-500">{message}</span> : null}
      {error ? <span className="text-xs text-red-600">{error}</span> : null}
    </div>
  )
}
