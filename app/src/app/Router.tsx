/**
 * Router — HashRouter 路由配置。
 *
 * WebView 从 index.html 加载，HashRouter 无需服务端路由。
 * T-M4-05c: 深链路由 — 通过 window.__aozoraNavigate__ 接收原生导航指令。
 */

import { HashRouter, Routes, Route, Navigate, useNavigate } from 'react-router'
import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { AppLayout } from '../components/Layout'
import { HomePage } from '../features/ledger/HomePage'
import { DetailPage } from '../features/ledger/DetailPage'
import { ReportsPage } from '../features/reports/ReportsPage'
import { MePage } from '../features/settings/MePage'
import { ReminderSettingsPage } from '../features/settings/ReminderSettingsPage'
import { ExportPage } from '../features/settings/ExportPage'
import { FeedbackPage } from '../features/settings/FeedbackPage'
import { AccountsPage } from '../features/settings/AccountsPage'
import { BooksPage } from '../features/settings/BooksPage'
import { CategoriesPage } from '../features/settings/CategoriesPage'
import { BudgetPage } from '../features/settings/BudgetPage'
import { AssistantPage } from '../features/assistant/AssistantPage'
import { OnboardingPage, isOnboardingCompleted } from '../features/onboarding/OnboardingPage'
import { LoginPage } from '../features/auth/LoginPage'
import { AuthLostBanner } from '../features/auth/AuthLostBanner'
import { PrivacyPage, TermsPage } from '../features/legal/LegalPage'

/** 默认路由目标：未完成首启则去 onboarding，否则去 home */
function DefaultRoute() {
  return <Navigate to={isOnboardingCompleted() ? '/home' : '/onboarding'} replace />
}

/** 路由守卫 — 未完成首启引导时重定向到 onboarding */
function ProtectedRoute({ children }: { children: ReactNode }) {
  if (!isOnboardingCompleted()) {
    return <Navigate to="/onboarding" replace />
  }
  return <>{children}</>
}

/** T-M4-05c: 深链路由 hook — 监听原生导航指令 */
function DeepLinkHandler() {
  const navigate = useNavigate()

  useEffect(() => {
    // 注册全局导航回调
    ;(window as any).__aozoraNavigate__ = (path: string) => {
      if (path && path.startsWith('/')) {
        navigate(path)
      }
    }
    return () => {
      delete (window as any).__aozoraNavigate__
    }
  }, [navigate])

  return null
}

export function Router() {
  return (
    <HashRouter>
      <DeepLinkHandler />
      <AuthLostBanner />
      <Routes>
        <Route path="/onboarding" element={<OnboardingPage />} />
        <Route path="/login" element={<LoginPage />} />
        {/* T-M5-04a F13: 法律文本页——公开路由，不依赖 onboarding 完成状态 */}
        <Route path="/privacy" element={<PrivacyPage />} />
        <Route path="/terms" element={<TermsPage />} />
        <Route path="/home" element={<ProtectedRoute><AppLayout><HomePage /></AppLayout></ProtectedRoute>} />
        <Route path="/detail" element={<ProtectedRoute><AppLayout><DetailPage /></AppLayout></ProtectedRoute>} />
        <Route path="/reports" element={<ProtectedRoute><AppLayout><ReportsPage /></AppLayout></ProtectedRoute>} />
        <Route path="/me" element={<ProtectedRoute><AppLayout><MePage /></AppLayout></ProtectedRoute>} />
        <Route path="/assistant" element={<ProtectedRoute><AssistantPage /></ProtectedRoute>} />
        <Route path="/settings/reminder" element={<ProtectedRoute><AppLayout><ReminderSettingsPage /></AppLayout></ProtectedRoute>} />
        <Route path="/settings/export" element={<ProtectedRoute><AppLayout><ExportPage /></AppLayout></ProtectedRoute>} />
        <Route path="/settings/feedback" element={<ProtectedRoute><AppLayout><FeedbackPage /></AppLayout></ProtectedRoute>} />
        <Route path="/settings/accounts" element={<ProtectedRoute><AppLayout><AccountsPage /></AppLayout></ProtectedRoute>} />
        <Route path="/settings/books" element={<ProtectedRoute><AppLayout><BooksPage /></AppLayout></ProtectedRoute>} />
        <Route path="/settings/categories" element={<ProtectedRoute><AppLayout><CategoriesPage /></AppLayout></ProtectedRoute>} />
        <Route path="/settings/budget" element={<ProtectedRoute><AppLayout><BudgetPage /></AppLayout></ProtectedRoute>} />
        <Route path="*" element={<DefaultRoute />} />
      </Routes>
    </HashRouter>
  )
}
