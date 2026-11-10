import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { fetchAudit, type AuditAction } from '../api/audit'
import { MonoId } from '../components/MonoId'
import { describeError } from '../lib/errors'
import { formatBeijing } from '../lib/time'

const ACTIONS: { value: AuditAction; label: string }[] = [
  { value: 'login', label: '登录' },
  { value: 'reveal', label: '查看明文' },
  { value: 'export', label: '导出' },
  { value: 'write', label: '写操作' },
]

const ACTION_STYLE: Record<string, string> = {
  login: 'bg-gray-100 text-gray-700',
  reveal: 'bg-amber-100 text-amber-800',
  export: 'bg-blue-100 text-blue-800',
  write: 'bg-red-100 text-red-800',
}

/** M8-10：审计记录（登录 / 查看明文 / 导出 / 写操作）。普通浏览不记录。 */
export function AuditPage() {
  const [action, setAction] = useState<AuditAction | ''>('')
  const [targetUserId, setTargetUserId] = useState('')
  const [cursors, setCursors] = useState<(string | null)[]>([null])

  const cursor = cursors[cursors.length - 1]
  const filters = {
    action: action || undefined,
    target_user_id: targetUserId.trim() || undefined,
  }
  const query = useQuery({
    queryKey: ['admin', 'audit', filters, cursor],
    queryFn: () => fetchAudit(filters, cursor),
  })

  const items = query.data?.items ?? []
  const nextCursor = query.data?.next_cursor ?? null

  function resetFilters() {
    setCursors([null])
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 bg-white p-4">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-600">动作</span>
          <select
            className="h-9 rounded border border-gray-300 px-2"
            value={action}
            onChange={(e) => {
              setAction(e.target.value as AuditAction | '')
              resetFilters()
            }}
          >
            <option value="">全部</option>
            {ACTIONS.map((a) => (
              <option key={a.value} value={a.value}>{a.label}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-600">目标用户 ID</span>
          <input
            className="h-9 w-80 rounded border border-gray-300 px-2 font-mono text-xs"
            placeholder="UUID（可空）"
            value={targetUserId}
            onChange={(e) => {
              setTargetUserId(e.target.value)
              resetFilters()
            }}
          />
        </label>
        <button
          type="button"
          className="h-9 rounded border border-gray-300 px-3 text-sm hover:bg-gray-50"
          onClick={resetFilters}
        >
          重置
        </button>
      </div>

      <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
        {query.isLoading ? (
          <div className="p-8 text-sm text-gray-500">加载中…</div>
        ) : query.isError ? (
          <div className="p-8 text-sm text-red-600">
            {describeError(query.error)}
            <button
              type="button"
              className="ml-3 underline"
              onClick={() => void query.refetch()}
            >
              重试
            </button>
          </div>
        ) : items.length === 0 ? (
          <div className="p-8 text-sm text-gray-500">没有审计记录</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
                <tr>
                  <th className="px-3 py-2">时间（北京）</th>
                  <th className="px-3 py-2">动作</th>
                  <th className="px-3 py-2">管理员</th>
                  <th className="px-3 py-2">目标用户</th>
                  <th className="px-3 py-2">会话</th>
                  <th className="px-3 py-2">IP</th>
                  <th className="px-3 py-2">详情</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id} className="border-t border-gray-100 align-top">
                    <td className="whitespace-nowrap px-3 py-2">
                      {formatBeijing(item.created_at)}
                    </td>
                    <td className="px-3 py-2">
                      <span className={`rounded px-2 py-0.5 text-xs ${ACTION_STYLE[item.action] ?? 'bg-gray-100 text-gray-700'}`}>
                        {item.action}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      {item.admin_id ? <MonoId value={item.admin_id} /> : '—'}
                    </td>
                    <td className="px-3 py-2">
                      {item.target_user_id ? <MonoId value={item.target_user_id} /> : '—'}
                    </td>
                    <td className="px-3 py-2">
                      {item.conversation_id ? <MonoId value={item.conversation_id} /> : '—'}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-gray-600">{item.ip ?? '—'}</td>
                    <td className="px-3 py-2">
                      <details>
                        <summary className="cursor-pointer text-xs text-blue-600">展开</summary>
                        <pre className="mt-1 max-w-[420px] overflow-x-auto whitespace-pre-wrap break-all rounded bg-gray-50 p-2 text-xs">
                          {JSON.stringify(item.detail, null, 2)}
                        </pre>
                      </details>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          className="h-8 rounded border border-gray-300 px-3 text-sm disabled:opacity-40"
          disabled={cursors.length <= 1}
          onClick={() => setCursors((prev) => prev.slice(0, -1))}
        >
          上一页
        </button>
        <button
          type="button"
          className="h-8 rounded border border-gray-300 px-3 text-sm disabled:opacity-40"
          disabled={!nextCursor}
          onClick={() => setCursors((prev) => [...prev, nextCursor])}
        >
          下一页
        </button>
        <span className="text-xs text-gray-500">第 {cursors.length} 页</span>
      </div>
    </div>
  )
}
