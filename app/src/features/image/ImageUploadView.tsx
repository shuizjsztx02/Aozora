/**
 * T-M4-04d: 图片上传进度/结果展示。
 *
 * 三种状态：上传中（进度条）、成功（附件信息）、失败（错误+重试/取消）。
 */

import type { ImagePickResult } from '../../platform/bridge/bridge-types'

interface ImageUploadViewProps {
  uploading: boolean
  progress: number
  attachment: ImagePickResult | null
  error: string | null
  onRetry: () => void
  onClear: () => void
  onPickAgain: () => void
}

const ERROR_MESSAGES: Record<string, string> = {
  cancelled: '已取消',
  compress_failed: '压缩失败',
  network: '网络错误',
  timeout: '上传超时',
  auth_lost: '登录已过期',
  file_too_large: '文件过大',
  server_error: '服务器错误',
  bridge_unavailable: '当前环境不支持',
  unknown: '未知错误',
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function ImageUploadView({
  uploading,
  progress,
  attachment,
  error,
  onRetry,
  onClear,
  onPickAgain,
}: ImageUploadViewProps) {
  // 上传中
  if (uploading && progress >= 0) {
    const pct = Math.round(progress * 100)
    return (
      <div
        className="rounded-xl p-3"
        style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
        data-testid="image-uploading"
      >
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs" style={{ color: 'var(--ink2)' }}>上传中...</span>
          <span className="text-xs money-num" style={{ color: 'var(--accent)' }}>{pct}%</span>
        </div>
        <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--glass-border)' }}>
          <div
            className="h-full rounded-full transition-all duration-200"
            style={{ width: `${pct}%`, background: 'var(--accent)' }}
          />
        </div>
      </div>
    )
  }

  // 等待选择器返回
  if (uploading) {
    return (
      <div
        className="rounded-xl p-3 text-xs text-center"
        style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)', color: 'var(--ink2)' }}
        data-testid="image-waiting"
      >
        等待选择图片...
      </div>
    )
  }

  // 上传成功
  if (attachment) {
    return (
      <div
        className="rounded-xl p-3 flex items-center justify-between"
        style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
        data-testid="image-success"
      >
        <div className="flex items-center gap-2">
          <span className="text-sm">✅</span>
          <span className="text-xs" style={{ color: 'var(--ink)' }}>
            已上传 {formatSize(attachment.fileSize)}
          </span>
        </div>
        <button
          onClick={onClear}
          className="text-xs px-2 py-1 rounded-md"
          style={{ color: 'var(--expense)' }}
          data-testid="image-clear"
        >
          移除
        </button>
      </div>
    )
  }

  // 上传失败
  if (error && error !== 'cancelled') {
    const msg = ERROR_MESSAGES[error] || '未知错误'
    return (
      <div
        className="rounded-xl p-3"
        style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
        data-testid="image-error"
      >
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs" style={{ color: 'var(--expense)' }}>❌ {msg}</span>
        </div>
        <div className="flex gap-2">
          <button
            onClick={onRetry}
            className="text-xs px-3 py-1.5 rounded-lg font-medium text-white"
            style={{ background: 'var(--accent)' }}
            data-testid="image-retry"
          >
            重试
          </button>
          <button
            onClick={onPickAgain}
            className="text-xs px-3 py-1.5 rounded-lg"
            style={{ color: 'var(--ink2)', border: '1px solid var(--glass-border)' }}
            data-testid="image-pick-again"
          >
            重新选择
          </button>
          <button
            onClick={onClear}
            className="text-xs px-3 py-1.5 rounded-lg"
            style={{ color: 'var(--ink3)' }}
            data-testid="image-dismiss"
          >
            取消
          </button>
        </div>
      </div>
    )
  }

  return null
}
