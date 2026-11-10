/** M8-10：审计记录查询（`GET /admin/audit`）。 */

import { apiRequest } from './client'

export type AuditAction = 'login' | 'reveal' | 'export' | 'write'

export interface AuditItem {
  id: string
  admin_id: string | null
  action: AuditAction | string
  target_user_id: string | null
  conversation_id: string | null
  detail: Record<string, unknown>
  ip: string | null
  created_at: string
}

export interface AuditListResponse {
  items: AuditItem[]
  next_cursor: string | null
}

export interface AuditFilters {
  action?: AuditAction
  target_user_id?: string
}

export function fetchAudit(
  filters: AuditFilters,
  cursor: string | null,
  limit = 50,
): Promise<AuditListResponse> {
  return apiRequest<AuditListResponse>('/admin/audit', {
    query: {
      action: filters.action,
      target_user_id: filters.target_user_id,
      cursor: cursor ?? undefined,
      limit,
    },
  })
}
