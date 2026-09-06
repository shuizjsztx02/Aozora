/**
 * AppLayout — 带底部导航的页面布局。
 */

import type { ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { BottomNav } from './BottomNav'

interface AppLayoutProps {
  children: ReactNode
}

export function AppLayout({ children }: AppLayoutProps) {
  return (
    <div className="relative z-[1] min-h-screen pb-16">
      {children}
      <BottomNav />
    </div>
  )
}

interface PageHeaderProps {
  title: string
  back?: boolean
  right?: ReactNode
}

export function PageHeader({ title, back, right }: PageHeaderProps) {
  const navigate = useNavigate()
  return (
    <header className="sticky top-0 z-10 px-4 py-3 flex items-center justify-between"
      style={{ background: 'var(--nav-bg)', backdropFilter: 'blur(12px)' }}>
      <div className="flex items-center gap-2">
        {back && (
          <button onClick={() => navigate(-1)} className="text-lg px-1" aria-label="返回">
            ←
          </button>
        )}
        <h1 className="text-lg font-semibold" style={{ color: 'var(--ink)' }}>{title}</h1>
      </div>
      {right && <div>{right}</div>}
    </header>
  )
}
