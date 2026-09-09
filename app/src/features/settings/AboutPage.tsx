/**
 * AboutPage — 关于页（T10-4 / 决策 #9）。
 *
 * 大图标 + 应用名 + 版本号 + 列表：隐私政策 / 用户协议 / 检查更新。
 * 检查更新为假实现：server 无版本 API，只提示已是最新版本（已知限制）。
 */

import { useState } from 'react'
import { useNavigate } from 'react-router'
import { PageHeader } from '../../components/Layout'
import { APP_VERSION } from '../../platform/app-version'
import appIcon from '../../assets/app-icon.png'

export function AboutPage() {
  const navigate = useNavigate()
  const [toast, setToast] = useState<string | null>(null)

  const handleCheckUpdate = () => {
    setToast(`已是最新版本 v${APP_VERSION}`)
    window.setTimeout(() => setToast(null), 2000)
  }

  return (
    <div className="min-h-screen">
      <PageHeader back />
      <div className="px-4 pt-6 pb-24 flex flex-col items-center">
        <img
          src={appIcon}
          alt="Aozora"
          className="w-24 h-24 rounded-2xl"
          data-testid="about-icon"
        />
        <div className="text-2xl font-bold mt-4" style={{ color: 'var(--ink)' }}>
          Aozora
        </div>
        <div className="text-xs mt-1" style={{ color: 'var(--ink3)' }}>
          版本 v{APP_VERSION}
        </div>

        <div className="glass-card overflow-hidden w-full mt-8" data-testid="about-list">
          <button
            onClick={() => navigate('/privacy')}
            className="w-full flex items-center gap-3 px-4 py-3 row-line text-left transition-colors hover:opacity-80"
          >
            <span className="text-lg">🔒</span>
            <span className="text-sm flex-1" style={{ color: 'var(--ink)' }}>隐私政策</span>
            <span className="text-ink3 text-xs">›</span>
          </button>
          <button
            onClick={() => navigate('/terms')}
            className="w-full flex items-center gap-3 px-4 py-3 row-line text-left transition-colors hover:opacity-80"
          >
            <span className="text-lg">📄</span>
            <span className="text-sm flex-1" style={{ color: 'var(--ink)' }}>用户协议</span>
            <span className="text-ink3 text-xs">›</span>
          </button>
          <button
            onClick={handleCheckUpdate}
            className="w-full flex items-center gap-3 px-4 py-3 row-line text-left transition-colors hover:opacity-80"
          >
            <span className="text-lg">🔄</span>
            <span className="text-sm flex-1" style={{ color: 'var(--ink)' }}>检查更新</span>
            <span className="text-ink3 text-xs">›</span>
          </button>
        </div>

        <div className="text-xs mt-6" style={{ color: 'var(--ink3)' }}>
          Aozora · 本地优先的 AI 记账
        </div>
      </div>

      {toast && (
        <div
          className="fixed left-1/2 -translate-x-1/2 bottom-24 z-50 px-4 py-2 rounded-full text-xs"
          style={{ background: 'var(--ink)', color: 'var(--bg)' }}
          data-testid="about-toast"
        >
          {toast}
        </div>
      )}
    </div>
  )
}
