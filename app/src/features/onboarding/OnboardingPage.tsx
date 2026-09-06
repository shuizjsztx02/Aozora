/**
 * OnboardingPage — 首启页。
 *
 * 品牌展示、隐私说明、游客/登录选择。
 */

import { useNavigate } from 'react-router'

const ONBOARDING_KEY = 'aozora-onboarding-completed'

export function OnboardingPage() {
  const navigate = useNavigate()

  const handleContinue = (login: boolean) => {
    try {
      localStorage.setItem(ONBOARDING_KEY, 'true')
    } catch {
      // 无痕模式/存储满时忽略，不阻塞用户进入
    }
    navigate(login ? '/login' : '/home')
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-6">
      {/* 品牌 */}
      <div className="text-center mb-8">
        <div className="text-6xl mb-4">🌤️</div>
        <h1 className="text-3xl font-bold mb-2" style={{ color: 'var(--ink)' }}>Aozora</h1>
        <p className="text-sm text-ink2">不用认真记账，也知道钱花哪了</p>
      </div>

      {/* 说明卡片 */}
      <div className="glass-card p-5 mb-6 max-w-sm w-full space-y-4">
        <div className="flex items-start gap-3">
          <span className="text-xl">🔒</span>
          <div>
            <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>隐私保护</div>
            <div className="text-xs text-ink2">你的数据仅存于你的设备与加密云端</div>
          </div>
        </div>
        <div className="flex items-start gap-3">
          <span className="text-xl">📡</span>
          <div>
            <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>离线可用</div>
            <div className="text-xs text-ink2">断网也能记账，联网后自动同步</div>
          </div>
        </div>
        <div className="flex items-start gap-3">
          <span className="text-xl">🤖</span>
          <div>
            <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>AI 助手</div>
            <div className="text-xs text-ink2">说句话就能记账，小贝帮你管账</div>
          </div>
        </div>
      </div>

      {/* 操作按钮 */}
      <div className="max-w-sm w-full space-y-3">
        <button
          onClick={() => handleContinue(false)}
          className="w-full py-3 rounded-xl text-white font-medium"
          style={{ background: 'var(--accent)' }}
          data-testid="guest-start-btn"
        >
          游客体验
        </button>
        <button
          onClick={() => handleContinue(true)}
          className="w-full py-3 rounded-xl font-medium"
          style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
          data-testid="login-btn"
        >
          登录 / 注册
        </button>
      </div>

      {/* 隐私声明 */}
      <p className="text-[10px] text-ink3 text-center mt-6 max-w-xs">
        继续使用即表示你同意我们的隐私政策与用户协议
      </p>
    </div>
  )
}

/** 检查是否已完成首启引导 */
export function isOnboardingCompleted(): boolean {
  try {
    return localStorage.getItem(ONBOARDING_KEY) === 'true'
  } catch {
    return false
  }
}
