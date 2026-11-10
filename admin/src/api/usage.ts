/**
 * T-M8-08：聚合统计与 `request_id` 反查的查询与类型。
 *
 * 契约事实源：`server/src/modules/admin/routes.py`
 * （`/admin/ai-usage/summary`、`/admin/requests/{request_id}`）与
 * `server/src/modules/admin/schemas.py`（`AdminUsageSummaryResponse`、
 * `AdminRequestResponse`）。本文件只读、不写。
 */

import { useQuery } from '@tanstack/react-query'
import { ApiError, apiRequest } from './client'

// ---------------------------------------------------------------------------
// 聚合统计
// ---------------------------------------------------------------------------

export type UsageGroupBy = 'user' | 'model' | 'day'

export interface UsageFilters {
  groupBy: UsageGroupBy
  /** 北京时间墙上时间对应的 ISO（带 `+08:00`），由 `beijingInputToIso` 生成。 */
  timeFrom?: string
  timeTo?: string
}

export interface UsageGroup {
  /** 分组键：user = 用户 ID；model = 模型名；day = 服务端 `date(created_at)`。 */
  key: string
  requests: number
  /** `ai_usage.tokens` 之和（原值，不做换算）。 */
  tokens: number
  /** `ai_usage.cost` 之和；单位微元（1e-6 元），按后端原值展示。 */
  cost: number
  /** 组内去重用户数（`COUNT(DISTINCT user_id)`）。 */
  users: number
}

export interface UsageSummaryResponse {
  group_by: UsageGroupBy
  items: UsageGroup[]
}

export function fetchUsageSummary(filters: UsageFilters): Promise<UsageSummaryResponse> {
  return apiRequest<UsageSummaryResponse>('/admin/ai-usage/summary', {
    query: {
      group_by: filters.groupBy,
      time_from: filters.timeFrom,
      time_to: filters.timeTo,
    },
  })
}

// ---------------------------------------------------------------------------
// request_id 反查
// ---------------------------------------------------------------------------

export interface RequestConversationHit {
  conversation_id: string
  user_id: string | null
  /** 该会话内命中该 request_id 的轮号（已排序去重）。 */
  turns: number[]
  event_count: number
  first_seq: number
  last_seq: number
  /** 命中事件的类型集合（去重；后端未提供按类型计数）。 */
  event_types: string[]
}

export interface RequestUsageItem {
  provider: string
  model: string
  tokens: number
  /** 微元（1e-6 元），按后端原值展示。 */
  cost: number
  /** 快路径（非 agent）行该值为 `NULL`。 */
  conversation_id: string | null
  created_at: string
}

export interface RequestLookupResponse {
  request_id: string
  /** 事件表精确命中；为空表示仅 `ai_usage` 命中。 */
  conversations: RequestConversationHit[]
  usage: RequestUsageItem[]
}

export function fetchRequestLookup(requestId: string): Promise<RequestLookupResponse> {
  return apiRequest<RequestLookupResponse>(
    `/admin/requests/${encodeURIComponent(requestId)}`,
  )
}

// ---------------------------------------------------------------------------
// 查询键与 hooks（本卡不接共享的 `hooks.ts`）
// ---------------------------------------------------------------------------

export const usageKeys = {
  summary: (filters: UsageFilters) => ['admin', 'usage-summary', filters] as const,
  request: (requestId: string) => ['admin', 'request-lookup', requestId] as const,
}

export function useUsageSummaryQuery(filters: UsageFilters) {
  return useQuery({
    queryKey: usageKeys.summary(filters),
    queryFn: () => fetchUsageSummary(filters),
  })
}

export function useRequestLookupQuery(requestId: string | null) {
  return useQuery({
    queryKey: usageKeys.request(requestId ?? ''),
    queryFn: () => {
      if (!requestId) throw new Error('requestId is required')
      return fetchRequestLookup(requestId)
    },
    enabled: Boolean(requestId),
    // 404 是确定性结果（查不到就是查不到），不重试；其余错误沿用全局策略。
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.status === 404) && failureCount < 1,
  })
}
