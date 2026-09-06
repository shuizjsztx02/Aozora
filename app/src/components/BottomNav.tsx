/**
 * BottomNav — 底部导航栏（nav-glass）。
 */

import { NavLink } from 'react-router'

const navItems = [
  { to: '/home', icon: '🏠', label: '首页' },
  { to: '/detail', icon: '📋', label: '明细' },
  { to: '/assistant', icon: '💬', label: '助手' },
  { to: '/reports', icon: '📊', label: '报表' },
  { to: '/me', icon: '👤', label: '我的' },
] as const

export function BottomNav() {
  return (
    <nav className="nav-glass fixed bottom-0 left-0 right-0 z-10 safe-bottom"
      aria-label="主导航"
      data-testid="bottom-nav">
      <div className="flex justify-around items-center h-14 max-w-lg mx-auto">
        {navItems.map(item => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `flex flex-col items-center gap-0.5 px-3 py-1 rounded-lg transition-colors ${
                isActive ? 'opacity-100' : 'opacity-50'
              }`
            }
            style={({ isActive }) => ({
              color: isActive ? 'var(--accent)' : 'var(--ink3)',
            })}
          >
            <span className="text-xl">{item.icon}</span>
            <span className="text-[10px]">{item.label}</span>
          </NavLink>
        ))}
      </div>
    </nav>
  )
}
