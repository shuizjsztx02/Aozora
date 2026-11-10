import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { HashRouter, Navigate, Route, Routes } from 'react-router'
import { ApiError } from './api/client'
import { AuthProvider } from './auth/AuthContext'
import { RequireAuth } from './auth/RequireAuth'
import { Shell } from './components/Shell'
import { ConversationReplayPage } from './pages/ConversationReplayPage'
import { ConversationsPage } from './pages/ConversationsPage'
import { LoginPage } from './pages/LoginPage'
import { PlaceholderPage } from './pages/PlaceholderPage'
import { RequestLookupPage } from './pages/RequestLookupPage'
import { UsagePage } from './pages/UsagePage'
import { UserDetailPage } from './pages/UserDetailPage'
import { UsersPage } from './pages/UsersPage'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 401 不重试（会话已失效）；其余错误最多重试 1 次。
      retry: (failureCount, error) =>
        !(error instanceof ApiError && error.status === 401) && failureCount < 1,
      refetchOnWindowFocus: false,
      staleTime: 15_000,
    },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <HashRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route element={<RequireAuth />}>
              <Route element={<Shell />}>
                <Route index element={<Navigate to="/users" replace />} />
                <Route path="/users" element={<UsersPage />} />
                <Route path="/users/:userId" element={<UserDetailPage />} />
                <Route path="/conversations" element={<ConversationsPage />} />
                <Route path="/replay" element={<ConversationReplayPage />} />
                <Route path="/replay/:conversationId" element={<ConversationReplayPage />} />
                <Route path="/usage" element={<UsagePage />} />
                <Route path="/requests" element={<RequestLookupPage />} />
                <Route
                  path="/export"
                  element={
                    <PlaceholderPage
                      title="导出"
                      task="8-09"
                      description="三种范围 × 明文/打码两种内容的流式 JSONL 导出与审计。"
                    />
                  }
                />
                <Route path="*" element={<Navigate to="/users" replace />} />
              </Route>
            </Route>
          </Routes>
        </HashRouter>
      </AuthProvider>
    </QueryClientProvider>
  )
}
