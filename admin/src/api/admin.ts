/** admin 读端点 + 登录（`server/src/modules/admin/routes.py`）。本卡不接写端点。 */

import { apiRequest } from './client'
import type {
  AdminConversationListResponse,
  AdminTokenResponse,
  AdminUserDetail,
  AdminUserListResponse,
  UserStatus,
} from './types'

/** 与 `routes.QUERY_MIN_CHARS` 对齐：更短的关键词后端直接 422。 */
export const QUERY_MIN_CHARS = 2

export function login(username: string, password: string): Promise<AdminTokenResponse> {
  return apiRequest<AdminTokenResponse>('/admin/auth/login', {
    method: 'POST',
    body: { username, password },
    skipAuthRedirect: true,
  })
}

export interface UserFilters {
  q?: string
  status?: UserStatus
  created_from?: string
  created_to?: string
}

export function fetchUsers(
  filters: UserFilters,
  cursor: string | null,
  limit = 50,
): Promise<AdminUserListResponse> {
  return apiRequest<AdminUserListResponse>('/admin/users', {
    query: {
      q: filters.q,
      status: filters.status,
      created_from: filters.created_from,
      created_to: filters.created_to,
      cursor,
      limit,
    },
  })
}

export function fetchUser(userId: string): Promise<AdminUserDetail> {
  return apiRequest<AdminUserDetail>(`/admin/users/${encodeURIComponent(userId)}`)
}

export interface ConversationFilters {
  q?: string
  user_id?: string
  time_from?: string
  time_to?: string
  include_archived?: boolean
}

export function fetchConversations(
  filters: ConversationFilters,
  cursor: string | null,
  limit = 50,
): Promise<AdminConversationListResponse> {
  return apiRequest<AdminConversationListResponse>('/admin/conversations', {
    query: {
      q: filters.q,
      user_id: filters.user_id,
      time_from: filters.time_from,
      time_to: filters.time_to,
      include_archived: filters.include_archived ?? false,
      cursor,
      limit,
    },
  })
}

export function fetchUserConversations(
  userId: string,
  filters: ConversationFilters,
  cursor: string | null,
  limit = 50,
): Promise<AdminConversationListResponse> {
  return apiRequest<AdminConversationListResponse>(
    `/admin/users/${encodeURIComponent(userId)}/conversations`,
    {
      query: {
        q: filters.q,
        time_from: filters.time_from,
        time_to: filters.time_to,
        include_archived: filters.include_archived ?? false,
        cursor,
        limit,
      },
    },
  )
}
