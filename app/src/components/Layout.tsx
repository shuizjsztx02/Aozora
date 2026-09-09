/**
 * AppLayout — 带底部导航的页面布局。
 * PageHeader — 子页面顶部导航栏（title 为空时仅渲染返回按钮）。
 */

import type { ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { BottomNav } from './BottomNav'

/* ─── AppLayout ─── */

interface AppLayoutProps {
  children: ReactNode
}

export function AppLayout({ children }: AppLayoutProps) {
  return (
    <div className="relative z-[1] min-h-screen pb-16 safe-top">
      {children}
      <BottomNav />
    </div>
  )
}

/* ─── PageHeader ─── */

interface PageHeaderProps {
  title?: string
  back?: boolean
  right?: ReactNode
}

export function PageHeader({ title, back, right }: PageHeaderProps) {
  const navigate = useNavigate()

  // 无标题 + 无返回 + 无右侧内容 = 不渲染
  // （T11-3 修复：此前漏判 right，ReportsPage 的 📤/📅 顶栏按钮从未渲染过）
  if (!title && !back && !right) return null

  // UI-15：子页返回栏（有返回、无标题）吸顶——fixed 栏自带安全区，
  // 文档流内用等高占位块补足初始空间，滚动时内容从毛玻璃栏下方穿过。
  const pinned = !!back && !title

  const bar = (
    <header
      className="flex items-center justify-between"
      style={{
        background: pinned ? undefined : title ? 'var(--nav-bg)' : 'transparent',
        backdropFilter: title ? 'blur(12px)' : undefined,
        minHeight: title ? 48 : 40,
        paddingTop: title ? undefined : 4,
        paddingBottom: title ? undefined : 4,
      }}
    >
      <div className="flex items-center gap-2 pl-3">
        {back && (
          <button
            onClick={() => navigate(-1)}
            className="flex items-center justify-center w-8 h-8 rounded-full transition-colors active:opacity-60"
            style={{ color: 'var(--ink)' }}
            aria-label="返回"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>
        )}
        {title && <h1 className="text-base font-semibold" style={{ color: 'var(--ink)' }}>{title}</h1>}
      </div>
      <div className="pr-3">{right}</div>
    </header>
  )

  if (pinned) {
    return (
      <>
        <div className="safe-header-fixed">{bar}</div>
        <div aria-hidden style={{ height: 40 }} />
      </>
    )
  }
  return bar
}
