/** 回放页左侧轮次列表：轮号 / 首条用户输入预览 / 模型与 token / 错误 / 事件数 / 起止时间。 */

import type { TurnItem } from '../api/replay'
import { cn } from '../lib/cn'
import { formatBeijing } from '../lib/time'
import { buttonSecondaryClass } from '../lib/ui'

interface TurnListProps {
  turns: TurnItem[]
  selectedTurn: number | null
  onSelect: (turn: number) => void
  isLoading: boolean
  error: string | null
  onRetry: () => void
}

export function TurnList({
  turns,
  selectedTurn,
  onSelect,
  isLoading,
  error,
  onRetry,
}: TurnListProps) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white">
      <div className="flex items-center justify-between border-b border-gray-200 px-3 py-2">
        <h2 className="text-sm font-semibold text-gray-900">轮次</h2>
        <span className="text-xs text-gray-500">{turns.length} 轮</span>
      </div>

      {error ? (
        <div className="space-y-2 px-3 py-6 text-center">
          <p className="text-sm text-red-600">{error}</p>
          <button type="button" className={buttonSecondaryClass} onClick={onRetry}>
            重试
          </button>
        </div>
      ) : isLoading && turns.length === 0 ? (
        <p className="px-3 py-6 text-center text-sm text-gray-500">加载中…</p>
      ) : turns.length === 0 ? (
        <p className="px-3 py-6 text-center text-sm text-gray-500">没有轮次</p>
      ) : (
        <ul className={cn('divide-y divide-gray-100', isLoading && 'opacity-60')}>
          {turns.map((item) => {
            const selected = item.turn === selectedTurn
            return (
              <li key={item.turn}>
                <button
                  type="button"
                  onClick={() => onSelect(item.turn)}
                  aria-current={selected ? 'true' : undefined}
                  className={cn(
                    'block w-full space-y-1 px-3 py-2 text-left transition-colors',
                    selected ? 'bg-blue-50' : 'hover:bg-gray-50',
                  )}
                >
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        'rounded px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset',
                        selected
                          ? 'bg-blue-100 text-blue-700 ring-blue-200'
                          : 'bg-gray-100 text-gray-600 ring-gray-200',
                      )}
                    >
                      第 {item.turn} 轮
                    </span>
                    {item.has_error ? (
                      <span className="rounded bg-red-50 px-1.5 py-0.5 text-xs font-medium text-red-700 ring-1 ring-inset ring-red-200">
                        报错
                      </span>
                    ) : null}
                    <span className="ml-auto text-xs text-gray-400">{item.event_count} 条事件</span>
                  </div>

                  <p
                    className="line-clamp-2 break-words text-sm text-gray-800"
                    title={item.first_user_input || undefined}
                  >
                    {item.first_user_input || '（无用户输入）'}
                  </p>

                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500">
                    {item.model ? (
                      <span className="rounded bg-emerald-50 px-1.5 py-0.5 font-medium text-emerald-700">
                        {item.model}
                      </span>
                    ) : (
                      <span className="text-gray-400">无模型记录</span>
                    )}
                    <span className="font-mono">{item.tokens.toLocaleString()} tokens</span>
                  </div>

                  <div className="text-xs text-gray-400">
                    {formatBeijing(item.started_at)} → {formatBeijing(item.ended_at)}
                  </div>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
