/**
 * ComingSoonPage — 占位页面，用于尚未实现的功能入口。
 *
 * 展示功能名称 + "即将上线"提示，带返回按钮。
 */

import { PageHeader } from '../../components/Layout'

interface ComingSoonPageProps {
  /** 页面标题 */
  title: string
  /** 功能描述 */
  description?: string
}

export function ComingSoonPage({ title, description }: ComingSoonPageProps) {
  return (
    <div className="min-h-screen">
      <PageHeader title={title} back />
      <div className="flex flex-col items-center justify-center py-20 px-6 text-center">
        <div className="text-5xl mb-4">🚧</div>
        <h2 className="text-lg font-semibold mb-2" style={{ color: 'var(--ink)' }}>
          {title}
        </h2>
        <p className="text-sm text-ink2">
          {description ?? '此功能即将上线，敬请期待'}
        </p>
        <div className="mt-4 text-xs px-3 py-1 rounded-full" style={{ background: 'var(--glass)', color: 'var(--ink3)', border: '1px solid var(--glass-border)' }}>
          即将上线
        </div>
      </div>
    </div>
  )
}
