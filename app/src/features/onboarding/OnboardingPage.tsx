/**
 * OnboardingPage — 首启页。
 *
 * T-M5-04a: 新增显式隐私同意步骤。
 * 品牌展示 → 隐私摘要 → "同意并继续" → 游客/登录选择。
 */

import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { NativeBridge } from '../../platform/bridge/native-bridge'

const ONBOARDING_KEY = 'aozora-onboarding-completed'
const CONSENT_KEY = 'aozora-consent-completed'
const POLICY_VERSION = 'v1'

export function OnboardingPage() {
  const navigate = useNavigate()
  const [step, setStep] = useState<'privacy' | 'choose'>('privacy')
  const [consenting, setConsenting] = useState(false)
  // T-M5-04a S0-3: 同意失败时保留错误并展示重试，不推进步骤。
  const [consentError, setConsentError] = useState<string | null>(null)
  // P3-2: 稳定化 bridge 引用，避免每次渲染都新建实例。
  const bridge = useMemo(() => new NativeBridge(), [])

  const handleConsent = async () => {
    setConsenting(true)
    setConsentError(null)
    try {
      if (bridge.isAvailable()) {
        // 原生环境：bridge 失败必须拦截，否则 Kotlin 侧 ConsentStore 不会 markConsented，
        // MainActivity 永远不会放行 sync，前端进入主页后数据同步不上去。
        await bridge.recordConsent({ policyVersion: POLICY_VERSION })
      }
      try {
        localStorage.setItem(CONSENT_KEY, 'true')
      } catch {
        /* 无痕模式/存储满时忽略 — 原生侧 ConsentStore 已是权威源 */
      }
      setStep('choose')
    } catch (e) {
      const msg = e instanceof Error ? e.message : '同意失败，请重试'
      setConsentError(msg)
    } finally {
      setConsenting(false)
    }
  }

  const handleContinue = (login: boolean) => {
    try {
      localStorage.setItem(ONBOARDING_KEY, 'true')
    } catch {
      // 无痕模式/存储满时忽略，不阻塞用户进入
    }
    navigate(login ? '/login' : '/home')
  }

  if (step === 'choose') {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center p-6">
        {/* 品牌 */}
        <div className="text-center mb-8">
          <div className="text-6xl mb-4">🌤️</div>
          <h1 className="text-3xl font-bold mb-2" style={{ color: 'var(--ink)' }}>Aozora</h1>
          <p className="text-sm text-ink2">不用认真记账，也知道钱花哪了</p>
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
      </div>
    )
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-6">
      {/* 品牌 */}
      <div className="text-center mb-8">
        <div className="text-6xl mb-4">🌤️</div>
        <h1 className="text-3xl font-bold mb-2" style={{ color: 'var(--ink)' }}>Aozora</h1>
        <p className="text-sm text-ink2">不用认真记账，也知道钱花哪了</p>
      </div>

      {/* 隐私摘要卡片 */}
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
            <div className="text-xs text-ink2">说句话就能记账，AI 生成内容旁会标注 "AI" 标识</div>
          </div>
        </div>
        <div className="border-t pt-3" style={{ borderColor: 'var(--glass-border)' }}>
          <p className="text-xs text-ink2 leading-relaxed">
            继续使用即表示你同意我们的{' '}
            <Link
              to="/privacy"
              state={{ fromOnboarding: true }}
              style={{ color: 'var(--accent)', textDecoration: 'underline' }}
            >
              隐私政策
            </Link>{' '}
            与{' '}
            <Link
              to="/terms"
              state={{ fromOnboarding: true }}
              style={{ color: 'var(--accent)', textDecoration: 'underline' }}
            >
              用户协议
            </Link>
            。我们仅在必要时采集数据，你可随时导出或删除。
          </p>
        </div>
      </div>

      {/* 同意按钮 */}
      <div className="max-w-sm w-full">
        <button
          onClick={handleConsent}
          disabled={consenting}
          className="w-full py-3 rounded-xl text-white font-medium"
          style={{ background: 'var(--accent)', opacity: consenting ? 0.7 : 1 }}
          data-testid="consent-btn"
        >
          {consenting ? '处理中…' : '同意并继续'}
        </button>
        {/* T-M5-04a S0-3: 失败时显示错误与重试按钮，不推进步骤 */}
        {consentError && (
          <div
            role="alert"
            className="mt-3 text-center"
            data-testid="consent-error"
          >
            <div className="text-xs" style={{ color: 'var(--danger, #d33)' }}>
              {consentError}
            </div>
            <button
              onClick={handleConsent}
              disabled={consenting}
              className="mt-2 text-xs underline"
              style={{ color: 'var(--ink2)' }}
              data-testid="consent-retry-btn"
            >
              重试
            </button>
          </div>
        )}
      </div>
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

/** T-M5-04a: 检查是否已同意隐私政策 */
export function hasConsentedPrivacy(): boolean {
  try {
    return localStorage.getItem(CONSENT_KEY) === 'true'
  } catch {
    return false
  }
}
