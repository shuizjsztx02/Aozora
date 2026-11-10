/**
 * 会话回放页的数据源：会话详情 / 轮次 / 事件。
 *
 * 契约以 `server/src/modules/admin/schemas.py` 与 `routes.py` 为准：
 * - 轮次与事件分页都是后端游标（`after_turn` / `after_seq`），禁止 offset；
 * - `/events` 默认 `reveal=false`（脱敏），`reveal=true` 才返回原文并写审计。
 */

import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { apiRequest } from './client'
import type { AdminConversationItem } from './types'

export interface ConversationDetail extends AdminConversationItem {
  updated_at: string
  summary_present: boolean
  search_text_chars: number
  turn_count: number
  event_count: number
  max_turn: number
  has_error: boolean
  models: string[]
  tokens: number
  cost: number
  first_event_at: string | null
  last_event_at: string | null
}

export interface TurnItem {
  turn: number
  /** 服务端已打码并截断的首条用户输入预览。 */
  first_user_input: string
  model: string | null
  tokens: number
  has_error: boolean
  event_count: number
  started_at: string | null
  ended_at: string | null
}

export interface TurnListResponse {
  conversation_id: string
  items: TurnItem[]
  next_after_turn: number | null
}

export interface EventItem {
  seq: number
  turn: number
  type: string
  time: string
  request_id: string | null
  data: Record<string, unknown>
}

export interface EventListResponse {
  conversation_id: string
  reveal: boolean
  items: EventItem[]
  next_after_seq: number | null
}

export const replayKeys = {
  detail: (conversationId: string | null) =>
    ['admin', 'replay', 'detail', conversationId] as const,
  turns: (conversationId: string | null, afterTurn: number | null, limit: number) =>
    ['admin', 'replay', 'turns', conversationId, afterTurn, limit] as const,
  events: (
    conversationId: string | null,
    turn: number | null,
    afterSeq: number | null,
    reveal: boolean,
    limit: number,
  ) => ['admin', 'replay', 'events', conversationId, turn, afterSeq, reveal, limit] as const,
}

export function fetchConversationDetail(conversationId: string): Promise<ConversationDetail> {
  return apiRequest<ConversationDetail>(
    `/admin/conversations/${encodeURIComponent(conversationId)}`,
  )
}

export function fetchTurns(
  conversationId: string,
  afterTurn: number | null,
  limit = 100,
): Promise<TurnListResponse> {
  return apiRequest<TurnListResponse>(
    `/admin/conversations/${encodeURIComponent(conversationId)}/turns`,
    { query: { after_turn: afterTurn, limit } },
  )
}

export function fetchEvents(
  conversationId: string,
  options: { turn: number; afterSeq: number | null; reveal: boolean; limit?: number },
): Promise<EventListResponse> {
  return apiRequest<EventListResponse>(
    `/admin/conversations/${encodeURIComponent(conversationId)}/events`,
    {
      query: {
        turn: options.turn,
        after_seq: options.afterSeq,
        reveal: options.reveal,
        limit: options.limit ?? 200,
      },
    },
  )
}

export function useConversationDetailQuery(conversationId: string | null) {
  return useQuery({
    queryKey: replayKeys.detail(conversationId),
    queryFn: () => {
      if (!conversationId) throw new Error('conversationId is required')
      return fetchConversationDetail(conversationId)
    },
    enabled: Boolean(conversationId),
  })
}

export function useTurnsQuery(
  conversationId: string | null,
  afterTurn: number | null,
  limit = 100,
) {
  return useQuery({
    queryKey: replayKeys.turns(conversationId, afterTurn, limit),
    queryFn: () => {
      if (!conversationId) throw new Error('conversationId is required')
      return fetchTurns(conversationId, afterTurn, limit)
    },
    enabled: Boolean(conversationId),
    placeholderData: keepPreviousData,
  })
}

export function useEventsQuery(
  conversationId: string | null,
  turn: number | null,
  afterSeq: number | null,
  reveal: boolean,
  limit = 200,
) {
  return useQuery({
    queryKey: replayKeys.events(conversationId, turn, afterSeq, reveal, limit),
    queryFn: () => {
      if (!conversationId || turn === null) throw new Error('conversationId and turn are required')
      return fetchEvents(conversationId, { turn, afterSeq, reveal, limit })
    },
    enabled: Boolean(conversationId) && turn !== null,
    placeholderData: keepPreviousData,
  })
}
