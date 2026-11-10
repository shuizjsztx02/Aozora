/** 后端 admin API 的响应类型——字段以 `server/src/modules/admin/schemas.py` 为准。 */

export type UserStatus = 'guest' | 'active' | 'frozen' | 'deactivated' | 'deleted'

export interface AdminTokenResponse {
  access_token: string
  token_type: string
  expires_in: number
  admin_id: string
  username: string
}

export interface AdminUserItem {
  id: string
  phone: string | null
  nickname: string | null
  status: UserStatus
  created_at: string
  admin_suspended_at: string | null
  ai_daily_quota_override: number | null
}

export interface AdminUserDetail extends AdminUserItem {
  updated_at: string
  admin_suspended_reason: string | null
  device_count: number
  conversation_count: number
  last_activity_at: string | null
}

export interface AdminUserListResponse {
  items: AdminUserItem[]
  next_cursor: string | null
}

export interface AdminConversationItem {
  id: string
  user_id: string
  book_id: string | null
  title: string
  last_active_at: string | null
  created_at: string
  archived_at: string | null
}

export interface AdminConversationListResponse {
  items: AdminConversationItem[]
  next_cursor: string | null
}
