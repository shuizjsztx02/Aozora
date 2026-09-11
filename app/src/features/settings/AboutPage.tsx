/**
 * AboutPage — 关于页（T10-4 / 决策 #9；T14-7 REQ-44 按用户设计稿改版）。
 *
 * 版式：天空横幅（T15-6 去 tagline，仅天空裁切图）→ 三枚玻璃胶囊按钮
 * （隐私政策 / 用户协议 / 检查更新）→ 版本行。检查更新仍为假实现：
 * server 无版本 API，只提示已是最新版本（已知限制）。
 */

import { useState } from 'react'
import { useNavigate } from 'react-router'
import { PageHeader } from '../../components/Layout'
import { APP_VERSION } from '../../platform/app-version'
import banner from '../../assets/about-banner.webp'

export function AboutPage() {
  const navigate = useNavigate()
  const [toast, setToast] = useState<string | null>(null)

  const handleCheckUpdate = () => {
    setToast(`当前 v${APP_VERSION}，已是最新版本`)
    window.setTimeout(() => setToast(null), 2000)
  }

  return (
    <div className="min-h-screen" style={{ background: 'linear-gradient(180deg, #f2faf6 0%, #eef6f4 60%, #e9f3f1 100%)' }}>
      <PageHeader back />
      {/* T15-6 天空横幅（去 tagline，仅天空区域裁切） */}
      <img src={banner} alt="" className="w-full block" data-testid="about-banner" />

      <div className="px-6 pt-2 pb-10 flex flex-col gap-5" data-testid="about-list">
        <button
          onClick={() => navigate('/privacy')}
          className="w-full flex items-center gap-3 px-5 py-4 rounded-full text-left transition-opacity active:opacity-70"
          style={{ background: 'rgba(255,255,255,0.72)', boxShadow: '0 2px 10px rgba(120,160,160,0.14)' }}
        >
          <span
            className="w-9 h-9 shrink-0 rounded-full flex items-center justify-center text-base"
            style={{ background: 'rgba(255,255,255,0.95)', boxShadow: '0 1px 4px rgba(120,160,160,0.18)' }}
          >🔒</span>
          <span className="text-base font-medium flex-1" style={{ color: '#2b4a4a' }}>隐私政策</span>
          <span className="text-lg" style={{ color: '#7ba5a5' }}>›</span>
        </button>
        <button
          onClick={() => navigate('/terms')}
          className="w-full flex items-center gap-3 px-5 py-4 rounded-full text-left transition-opacity active:opacity-70"
          style={{ background: 'rgba(255,255,255,0.72)', boxShadow: '0 2px 10px rgba(120,160,160,0.14)' }}
        >
          <span
            className="w-9 h-9 shrink-0 rounded-full flex items-center justify-center text-base"
            style={{ background: 'rgba(255,255,255,0.95)', boxShadow: '0 1px 4px rgba(120,160,160,0.18)' }}
          >📄</span>
          <span className="text-base font-medium flex-1" style={{ color: '#2b4a4a' }}>用户协议</span>
          <span className="text-lg" style={{ color: '#7ba5a5' }}>›</span>
        </button>
        <button
          onClick={handleCheckUpdate}
          className="w-full flex items-center gap-3 px-5 py-4 rounded-full text-left transition-opacity active:opacity-70"
          style={{ background: 'rgba(255,255,255,0.72)', boxShadow: '0 2px 10px rgba(120,160,160,0.14)' }}
        >
          <span
            className="w-9 h-9 shrink-0 rounded-full flex items-center justify-center text-base"
            style={{ background: 'rgba(255,255,255,0.95)', boxShadow: '0 1px 4px rgba(120,160,160,0.18)' }}
          >🔄</span>
          <span className="text-base font-medium flex-1" style={{ color: '#2b4a4a' }}>检查更新</span>
        </button>
      </div>

      <div className="text-center pb-10 text-sm" style={{ color: '#5f8585' }}>
        版本 v{APP_VERSION}
      </div>

      {toast && (
        <div
          className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-[80] px-5 py-3 rounded-2xl text-sm"
          style={{
            background: 'rgba(255,255,255,0.82)',
            backdropFilter: 'blur(12px)',
            color: '#2b4a4a',
            boxShadow: '0 4px 20px rgba(120,160,160,0.18)',
          }}
          data-testid="about-toast"
        >
          {toast}
        </div>
      )}
    </div>
  )
}
