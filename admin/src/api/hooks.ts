/** TanStack Query 封装：查询键 + 翻页时保留上一页数据。 */

import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  fetchConversations,
  fetchUser,
  fetchUserConversations,
  fetchUsers,
  type ConversationFilters,
  type UserFilters,
} from './admin'

export const adminKeys = {
  users: (filters: UserFilters, cursor: string | null) => ['admin', 'users', filters, cursor] as const,
  user: (userId: string | null) => ['admin', 'user', userId] as const,
  userConversations: (userId: string | null, filters: ConversationFilters, cursor: string | null) =>
    ['admin', 'user', userId, 'conversations', filters, cursor] as const,
  conversations: (filters: ConversationFilters, cursor: string | null) =>
    ['admin', 'conversations', filters, cursor] as const,
}

export function useUsersQuery(filters: UserFilters, cursor: string | null) {
  return useQuery({
    queryKey: adminKeys.users(filters, cursor),
    queryFn: () => fetchUsers(filters, cursor),
    placeholderData: keepPreviousData,
  })
}

export function useUserQuery(userId: string | null) {
  return useQuery({
    queryKey: adminKeys.user(userId),
    queryFn: () => {
      if (!userId) throw new Error('userId is required')
      return fetchUser(userId)
    },
    enabled: Boolean(userId),
  })
}

export function useConversationsQuery(filters: ConversationFilters, cursor: string | null) {
  return useQuery({
    queryKey: adminKeys.conversations(filters, cursor),
    queryFn: () => fetchConversations(filters, cursor),
    placeholderData: keepPreviousData,
  })
}

export function useUserConversationsQuery(
  userId: string | null,
  filters: ConversationFilters,
  cursor: string | null,
) {
  return useQuery({
    queryKey: adminKeys.userConversations(userId, filters, cursor),
    queryFn: () => {
      if (!userId) throw new Error('userId is required')
      return fetchUserConversations(userId, filters, cursor)
    },
    enabled: Boolean(userId),
    placeholderData: keepPreviousData,
  })
}
