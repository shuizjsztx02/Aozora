/**
 * T-M8-09 导出：`POST /admin/export`（流式 NDJSON）。
 *
 * 契约以 `server/src/modules/admin/routes.py::export_data` 为准：
 * - 请求体 `{ scope, format, content, ...筛选参数 }`；
 * - 成功：`application/x-ndjson` + `Content-Disposition: attachment; filename="…"`；
 * - 超限：422 `export_too_large`（提示缩小范围后分批）。
 *
 * 导出不走 `apiRequest`：它返回的是文件流而不是 JSON，且需要读响应头里的文件名。
 */

import { API_BASE, ApiError, notifyUnauthorized, parseError } from './client'

export type ExportScope = 'filtered' | 'all' | 'user'
export type ExportFormat = 'plain' | 'masked'
export type ExportContent = 'events' | 'conversations'

export interface ExportRequest {
  scope: ExportScope
  format: ExportFormat
  content?: ExportContent
  user_id?: string
  conversation_id?: string
  time_from?: string
  time_to?: string
  q?: string
  include_archived?: boolean
}

export interface ExportDownload {
  filename: string
  bytes: number
}

const FALLBACK_FILENAME = 'aozora-export.jsonl'

/** `attachment; filename="aozora-export-….jsonl"` → 文件名。 */
function filenameFromDisposition(value: string | null): string | null {
  if (!value) return null
  const match = /filename="?([^";]+)"?/i.exec(value)
  return match ? match[1] : null
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB']
  let value = bytes / 1024
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  return `${value.toFixed(1)} ${units[index]}`
}

/**
 * 发起导出并把响应写成下载文件。
 *
 * `onProgress` 报告**已接收字节数**（服务端流式生成，没有 Content-Length，
 * 只能报告累计值）。
 */
export async function downloadExport(
  request: ExportRequest,
  token: string,
  options: { onProgress?: (bytes: number) => void } = {},
): Promise<ExportDownload> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}/admin/export`, {
      method: 'POST',
      headers: {
        Accept: 'application/x-ndjson',
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(request),
    })
  } catch {
    throw new ApiError(0, 'network_error', '无法连接服务端，请检查网络或 API 地址')
  }

  if (!response.ok) {
    const error = await parseError(response)
    if (response.status === 401) notifyUnauthorized()
    throw error
  }

  const filename =
    filenameFromDisposition(response.headers.get('content-disposition')) ?? FALLBACK_FILENAME

  // 逐块读，边读边报进度：导出文件可达上百 MB，不能等整个 Blob 落地才有反馈。
  const chunks: BlobPart[] = []
  let bytes = 0
  if (response.body) {
    const reader = response.body.getReader()
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      chunks.push(value)
      bytes += value.byteLength
      options.onProgress?.(bytes)
    }
  } else {
    const fallback = await response.blob()
    chunks.push(fallback)
    bytes = fallback.size
    options.onProgress?.(bytes)
  }

  const blob = new Blob(chunks, { type: 'application/x-ndjson' })
  triggerDownload(blob, filename)
  return { filename, bytes }
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}
