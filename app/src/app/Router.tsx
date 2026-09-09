/**
 * Router — HashRouter 路由配置。
 *
 * WebView 从 index.html 加载，HashRouter 无需服务端路由。
 * T-M4-05c: 深链路由 — 通过 window.__aozoraNavigate__ 接收原生导航指令。
 */

import { HashRouter, Routes, Route, Navigate, useNavigate, useLocation } from 'react-router'
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
import { SavingsPage } from '../features/settings/SavingsPage'
import { ScheduledPage } from '../features/settings/ScheduledPage'
import { SecurityPage } from '../features/settings/SecurityPage'
import { WidgetSettingsPage } from '../features/settings/WidgetSettingsPage'
import { UserSettingsPage } from '../features/settings/UserSettingsPage'
import { PersonasPage } from '../features/settings/PersonasPage'
import { AboutPage } from '../features/settings/AboutPage'
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

/**
 * 第八轮 #1（UI-17）：路由切换复位文档滚动位置。
 *
 * HashRouter 不重置 scrollY——从"我的"页滚动到中下部"更多设置"分组再进子页时，
 * 文档残留滚动会叠加到子页首帧（吸顶栏 fixed 不受影响，但内容流仍从旧 scrollY 起），
 * 观感即"顶部安全区没生效"。切页即归零，与账本/账户"从近顶部进入"的观感对齐。
 */
function ScrollToTop() {
  const { pathname } = useLocation()
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [pathname])
  return null
}

// 供单测直接渲染
export { ScrollToTop }

export function Router() {
  return (
    <HashRouter>
      <DeepLinkHandler />
      <ScrollToTop />
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
        <Route path="/settings/savings" element={<ProtectedRoute><AppLayout><SavingsPage /></AppLayout></ProtectedRoute>} />
        {/* 第十一轮 T11-5（REQ-26 / 决策 #9）：定时记账规则管理 */}
        <Route path="/settings/scheduled" element={<ProtectedRoute><AppLayout><ScheduledPage /></AppLayout></ProtectedRoute>} />
        <Route path="/settings/security" element={<ProtectedRoute><AppLayout><SecurityPage /></AppLayout></ProtectedRoute>} />
        <Route path="/settings/widget" element={<ProtectedRoute><AppLayout><WidgetSettingsPage /></AppLayout></ProtectedRoute>} />
        {/* 第十一轮 T11-6（REQ-27 / 决策 #9）：用户设定（画像编辑 → AI context） */}
        <Route path="/settings/user-prefs" element={<ProtectedRoute><AppLayout><UserSettingsPage /></AppLayout></ProtectedRoute>} />
        {/* 第十一轮 T11-7（REQ-28 / 决策 #8）：聊天角色管理（入口在聊天页 header） */}
        <Route path="/settings/personas" element={<ProtectedRoute><AppLayout><PersonasPage /></AppLayout></ProtectedRoute>} />
        <Route path="/settings/about" element={<ProtectedRoute><AppLayout><AboutPage /></AppLayout></ProtectedRoute>} />
        <Route path="*" element={<DefaultRoute />} />
      </Routes>
    </HashRouter>
  )
}
