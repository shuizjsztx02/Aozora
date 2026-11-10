import { NavLink, Outlet } from 'react-router'
import { useAuth } from '../auth/AuthContext'
import { cn } from '../lib/cn'

interface NavItem {
  to: string
  label: string
  /** 未交付的占位页标注所属子卡，避免误以为可用。 */
  pendingTask?: string
}

const NAV_GROUPS: { title: string; items: NavItem[] }[] = [
  {
    title: '检索',
    items: [
      { to: '/users', label: '用户检索' },
      { to: '/conversations', label: '会话检索' },
    ],
  },
  {
    title: '回放',
    items: [{ to: '/replay', label: '会话回放' }],
  },
  {
    title: '数据',
    items: [
      { to: '/usage', label: '用量统计' },
      { to: '/requests', label: '请求反查' },
      { to: '/export', label: '导出', pendingTask: '8-09' },
    ],
  },
]

/** 后台骨架：顶栏（管理员名 / 登出）+ 左侧分组菜单 + 内容区。 */
export function Shell() {
  const { session, logout } = useAuth()

  return (
    <div className="min-h-screen bg-gray-100 text-gray-900">
      <header className="sticky top-0 z-20 border-b border-gray-200 bg-white">
        <div className="mx-auto flex w-full max-w-[1600px] flex-wrap items-center justify-between gap-2 px-4 py-3">
          <div className="text-base font-semibold">Aozora 运营后台</div>
          <div className="flex items-center gap-3 text-sm">
            <span className="text-gray-600">{session?.username ?? '—'}</span>
            <button
              type="button"
              onClick={logout}
              className="rounded-md border border-gray-300 px-2.5 py-1 text-gray-700 hover:bg-gray-50"
            >
              登出
            </button>
          </div>
        </div>
      </header>

      <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-4 px-4 py-4 md:flex-row">
        <nav className="rounded-lg border border-gray-200 bg-white p-3 md:w-48 md:shrink-0 md:self-start">
          {NAV_GROUPS.map((group) => (
            <div key={group.title} className="mb-3 last:mb-0">
              <div className="mb-1 text-xs font-medium tracking-wide text-gray-500">
                {group.title}
              </div>
              <div className="flex flex-wrap gap-1 md:flex-col md:gap-0.5">
                {group.items.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    className={({ isActive }) =>
                      cn(
                        'rounded px-2 py-1.5 text-sm',
                        isActive ? 'bg-blue-50 text-blue-700' : 'text-gray-700 hover:bg-gray-50',
                      )
                    }
                  >
                    {item.label}
                    {item.pendingTask ? (
                      <span className="ml-1 text-xs text-gray-400">{item.pendingTask}</span>
                    ) : null}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>

        {/* min-w-0 让表格在自身容器内横向滚动，页面不出现横向滚动条。 */}
        <main className="min-w-0 flex-1 space-y-4">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
