/**
 * BottomNav — 底部导航栏。
 *
 * 五个导航项：首页 / 明细 / (居中突出) / 报表 / 我的
 * 图标为 outline SVG 风格，中间 ＋ 按钮以白色大圆圈突出显示。
 */

import { NavLink, useNavigate } from 'react-router'

/* ─── SVG outline 图标 ─── */

function HomeIcon({ active }: { active: boolean }) {
  const stroke = active ? 'var(--accent)' : 'var(--ink3)'
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none"
      stroke={stroke} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 10.5L12 3l9 7.5V20a1 1 0 01-1 1H4a1 1 0 01-1-1V10.5z" />
      <path d="M9 21V14h6v7" />
    </svg>
  )
}

function ListIcon({ active }: { active: boolean }) {
  const stroke = active ? 'var(--accent)' : 'var(--ink3)'
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none"
      stroke={stroke} strokeWidth="1.8" strokeLinecap="round">
      <line x1="8" y1="6" x2="21" y2="6" />
      <line x1="8" y1="12" x2="21" y2="12" />
      <line x1="8" y1="18" x2="21" y2="18" />
      <circle cx="4" cy="6" r="1" fill={stroke} stroke="none" />
      <circle cx="4" cy="12" r="1" fill={stroke} stroke="none" />
      <circle cx="4" cy="18" r="1" fill={stroke} stroke="none" />
    </svg>
  )
}

function ChatIcon({ active }: { active: boolean }) {
  const stroke = active ? 'var(--accent)' : 'var(--ink3)'
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none"
      stroke={stroke} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z" />
    </svg>
  )
}

function PieIcon({ active }: { active: boolean }) {
  const stroke = active ? 'var(--accent)' : 'var(--ink3)'
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none"
      stroke={stroke} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21.21 15.89A10 10 0 118 2.83" />
      <path d="M22 12A10 10 0 0012 2v10z" />
    </svg>
  )
}

function PersonIcon({ active }: { active: boolean }) {
  const stroke = active ? 'var(--accent)' : 'var(--ink3)'
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none"
      stroke={stroke} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </svg>
  )
}

const navItems = [
  { to: '/home', label: '首页', Icon: HomeIcon },
  { to: '/detail', label: '明细', Icon: ListIcon },
  { to: '/assistant', label: '聊天', Icon: ChatIcon },
  { to: '/reports', label: '报表', Icon: PieIcon },
  { to: '/me', label: '我的', Icon: PersonIcon },
] as const

export function BottomNav() {
  const navigate = useNavigate()

  return (
    <nav className="nav-glass fixed bottom-0 left-0 right-0 z-10 safe-bottom"
      aria-label="主导航" data-testid="bottom-nav">
      <div className="flex justify-around items-center h-14 max-w-lg mx-auto relative">
        {navItems.map(({ to, label, Icon }, idx) => {
          const isCenter = idx === 2

          if (isCenter) {
            // 中间 ＋ 按钮：大白色圆圈，突出于导航栏之上
            return (
              <button
                key={to}
                onClick={() => navigate(to)}
                className="flex flex-col items-center justify-center gap-0.5 rounded-full shadow-lg transition-transform active:scale-95"
                style={{
                  width: 56,
                  height: 56,
                  background: '#fff',
                  color: 'var(--ink)',
                  marginTop: -12,
                }}
                aria-label="快速记账"
              >
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none"
                  stroke="var(--ink)" strokeWidth="2" strokeLinecap="round">
                  <line x1="12" y1="5" x2="12" y2="19" />
                  <line x1="5" y1="12" x2="19" y2="12" />
                </svg>
              </button>
            )
          }

          return (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex flex-col items-center justify-center gap-0.5 px-3 py-1 rounded-lg transition-colors ${
                  isActive ? 'opacity-100' : 'opacity-60'
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <Icon active={isActive} />
                  <span
                    className="text-[10px] leading-tight"
                    style={{ color: isActive ? 'var(--accent)' : 'var(--ink3)' }}
                  >
                    {label}
                  </span>
                  {isActive && (
                    <span
                      className="block w-1 h-1 rounded-full mt-0.5"
                      style={{ background: 'var(--accent)' }}
                    />
                  )}
                </>
              )}
            </NavLink>
          )
        })}
      </div>
    </nav>
  )
}
