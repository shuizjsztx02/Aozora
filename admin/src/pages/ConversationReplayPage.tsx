/**
 * T-M8-07 会话回放页（核心）：左侧轮次列表 + 右侧事件时间轴。
 *
 * 数据流：
 * 1. `GET /admin/conversations/{id}` — 会话元信息与聚合（用于头部与保留期空态）；
 * 2. `GET /admin/conversations/{id}/turns?after_turn=&limit=` — 轮次列表（keyset）；
 * 3. `GET /admin/conversations/{id}/events?turn=&after_seq=&reveal=&limit=` —
 *    选中轮次的事件流（keyset）。默认 `reveal=false`，点「查看原文」后以
 *    `reveal=true` 重新拉取（服务端写 reveal 审计）。
 *
 * 路由：入口由主会话统一接线；本组件兼容 `/replay/:conversationId` 与
 * `/replay?conversationId=` 两种取参方式。只读，无导出入口（8-09）。
 */

import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import {
  type TurnItem,
  type EventItem,
  useConversationDetailQuery,
  useEventsQuery,
  useTurnsQuery,
} from '../api/replay'
import { DetailRow } from '../components/DetailDrawer'
import { EventTimeline } from '../components/EventTimeline'
import { Pagination } from '../components/Pagination'
import { TurnList } from '../components/TurnList'
import { cn } from '../lib/cn'
import { describeError } from '../lib/errors'
import { useKeysetPager } from '../lib/pager'
import { formatBeijing } from '../lib/time'
import { buttonPrimaryClass, buttonSecondaryClass, linkClass } from '../lib/ui'

const EMPTY_TURNS: TurnItem[] = []
const EMPTY_EVENTS: EventItem[] = []

/** 页内游标栈存的是数字串（`after_turn` / `after_seq`），这里还原成数字。 */
function parseCursor(cursor: string | null): number | null {
  if (cursor === null) return null
  const value = Number(cursor)
  return Number.isFinite(value) ? value : null
}

export function ConversationReplayPage() {
  const params = useParams<{ conversationId?: string }>()
  const [searchParams] = useSearchParams()
  const conversationId = params.conversationId ?? searchParams.get('conversationId') ?? null

  const turnPager = useKeysetPager()
  const eventPager = useKeysetPager()
  const { reset: resetTurnPager } = turnPager
  const { reset: resetEventPager } = eventPager

  const [selectedTurn, setSelectedTurn] = useState<number | null>(null)
  const [revealState, setRevealState] = useState(false)

  // 切换会话时清空所有页内游标与选择（同一路由元素会被复用）。
  useEffect(() => {
    resetTurnPager()
    resetEventPager()
    setSelectedTurn(null)
    setRevealState(false)
  }, [conversationId, resetTurnPager, resetEventPager])

  const afterTurn = parseCursor(turnPager.cursor)
  const afterSeq = parseCursor(eventPager.cursor)

  const detailQuery = useConversationDetailQuery(conversationId)
  const turnsQuery = useTurnsQuery(conversationId, afterTurn)
  const eventsQuery = useEventsQuery(conversationId, selectedTurn, afterSeq, revealState)

  const turns = turnsQuery.data?.items ?? EMPTY_TURNS
  const events = selectedTurn !== null ? (eventsQuery.data?.items ?? EMPTY_EVENTS) : EMPTY_EVENTS

  // 轮次列表变化后保证选中项落在当前页；切换会话/翻页时默认选第一轮。
  useEffect(() => {
    if (turns.length === 0) return
    if (selectedTurn === null || !turns.some((item) => item.turn === selectedTurn)) {
      setSelectedTurn(turns[0].turn)
      resetEventPager()
    }
  }, [turns, selectedTurn, resetEventPager])

  function selectTurn(turn: number) {
    setSelectedTurn(turn)
    resetEventPager()
  }

  if (!conversationId) {
    return (
      <section className="space-y-3">
        <h1 className="text-lg font-semibold text-gray-900">会话回放</h1>
        <div className="rounded-lg border border-gray-200 bg-white px-4 py-10 text-center">
          <p className="text-sm text-gray-600">未指定会话。</p>
          <Link to="/conversations" className={cn(linkClass, 'mt-2 inline-block')}>
            去会话检索选择一个会话
          </Link>
        </div>
      </section>
    )
  }

  const detail = detailQuery.data
  const showRetentionEmpty =
    Boolean(detail) &&
    Boolean(turnsQuery.data) &&
    !turnsQuery.error &&
    !turnsQuery.isFetching &&
    turns.length === 0

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold text-gray-900">会话回放</h1>
        <div className="flex flex-wrap gap-3">
          <Link to="/conversations" className={linkClass}>
            ← 返回会话检索
          </Link>
          {detail ? (
            <Link to={`/users/${detail.user_id}`} className={linkClass}>
              查看用户
            </Link>
          ) : null}
        </div>
      </div>

      {detailQuery.isPending ? (
        <div className="rounded-lg border border-gray-200 bg-white px-4 py-8 text-center text-sm text-gray-500">
          加载中…
        </div>
      ) : detailQuery.error ? (
        <div className="rounded-lg border border-gray-200 bg-white px-4 py-8 text-center">
          <p className="text-sm text-red-600">{describeError(detailQuery.error)}</p>
          <button
            type="button"
            className={cn(buttonSecondaryClass, 'mt-2')}
            onClick={() => void detailQuery.refetch()}
          >
            重试
          </button>
        </div>
      ) : detail ? (
        <div className="rounded-lg border border-gray-200 bg-white p-4">
          <div className="grid grid-cols-1 gap-x-8 lg:grid-cols-2">
            <div>
              <DetailRow label="标题" value={detail.title || '（无标题）'} />
              <DetailRow label="会话 ID" value={detail.id} mono />
              <DetailRow label="用户 ID" value={detail.user_id} mono />
              <DetailRow label="创建时间" value={formatBeijing(detail.created_at)} />
              <DetailRow label="最后活跃" value={formatBeijing(detail.last_active_at)} />
              <DetailRow label="更新时间" value={formatBeijing(detail.updated_at)} />
              <DetailRow label="归档" value={detail.archived_at ? '已归档' : '否'} />
            </div>
            <div>
              <DetailRow label="轮次数" value={detail.turn_count} />
              <DetailRow label="事件数" value={detail.event_count} />
              <DetailRow
                label="模型"
                value={detail.models.length > 0 ? detail.models.join('、') : '—'}
              />
              <DetailRow label="tokens" value={detail.tokens.toLocaleString()} />
              <DetailRow
                label="是否报错"
                value={
                  detail.has_error ? (
                    <span className="font-medium text-red-600">是</span>
                  ) : (
                    '否'
                  )
                }
              />
              <DetailRow label="首次事件" value={formatBeijing(detail.first_event_at)} />
              <DetailRow label="末次事件" value={formatBeijing(detail.last_event_at)} />
            </div>
          </div>
        </div>
      ) : null}

      {showRetentionEmpty ? (
        <div className="rounded-lg border border-gray-200 bg-white px-4 py-10 text-center">
          <p className="text-sm font-medium text-gray-700">该会话没有可回放的事件</p>
          <p className="mt-1 text-xs text-gray-500">
            元信息仍保留（创建于 {formatBeijing(detail?.created_at)}）。事件可能已超出保留期
            （按整轮清理），或该会话尚未产生对话。
          </p>
        </div>
      ) : detail ? (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-[320px_minmax(0,1fr)]">
          <div className="space-y-2">
            <TurnList
              turns={turns}
              selectedTurn={selectedTurn}
              onSelect={selectTurn}
              isLoading={turnsQuery.isFetching}
              error={turnsQuery.error ? describeError(turnsQuery.error) : null}
              onRetry={() => void turnsQuery.refetch()}
            />
            <Pagination
              pageIndex={turnPager.pageIndex}
              rowCount={turns.length}
              hasPrev={turnPager.hasPrev}
              hasNext={turnsQuery.data?.next_after_turn != null}
              isLoading={turnsQuery.isFetching}
              onPrev={turnPager.goPrev}
              onNext={() => {
                const next = turnsQuery.data?.next_after_turn
                if (next != null) turnPager.goNext(String(next))
              }}
            />
          </div>

          <div className="min-w-0 space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2">
              <div className="text-sm text-gray-700">
                {selectedTurn === null ? '未选择轮次' : `第 ${selectedTurn} 轮`}
                <span
                  className={cn(
                    'ml-2 text-xs',
                    revealState ? 'font-medium text-amber-700' : 'text-gray-400',
                  )}
                >
                  {revealState ? '原文模式' : '脱敏模式'}
                </span>
              </div>
              <button
                type="button"
                className={revealState ? buttonSecondaryClass : buttonPrimaryClass}
                onClick={() => setRevealState((value) => !value)}
                disabled={selectedTurn === null}
              >
                {revealState ? '恢复脱敏' : '查看原文'}
              </button>
            </div>

            <EventTimeline
              events={events}
              turn={selectedTurn}
              reveal={eventsQuery.data?.reveal === true}
              hasMore={eventsQuery.data?.next_after_seq != null}
              isLoading={selectedTurn !== null && eventsQuery.isFetching}
              error={
                selectedTurn !== null && eventsQuery.error
                  ? describeError(eventsQuery.error)
                  : null
              }
              onRetry={() => void eventsQuery.refetch()}
            />

            <Pagination
              pageIndex={eventPager.pageIndex}
              rowCount={events.length}
              hasPrev={eventPager.hasPrev}
              hasNext={eventsQuery.data?.next_after_seq != null}
              isLoading={eventsQuery.isFetching}
              onPrev={eventPager.goPrev}
              onNext={() => {
                const next = eventsQuery.data?.next_after_seq
                if (next != null) eventPager.goNext(String(next))
              }}
            />
          </div>
        </div>
      ) : null}
    </section>
  )
}
