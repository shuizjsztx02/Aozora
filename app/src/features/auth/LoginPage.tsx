/**
 * LoginPage — 登录页。
 *
 * 手机验证码登录 + 游客升级说明。
 * 注：真实 SMS 供应商待 D-EXEC-03 确认，当前为 UI 骨架。
 */

import { useState } from 'react'
import { useNavigate } from 'react-router'

export function LoginPage() {
  const navigate = useNavigate()
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [step, setStep] = useState<'phone' | 'code'>('phone')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSendCode = async () => {
    if (!phone || phone.length < 11) {
      setError('请输入正确的手机号')
      return
    }
    setError('')
    setLoading(true)
    try {
      // 开发模式：bridge 可能不支持 auth.login，直接跳转
      // 真实 SMS 待 D-EXEC-03
      setStep('code')
    } catch {
      setError('发送验证码失败，请稍后重试')
    } finally {
      setLoading(false)
    }
  }

  const handleLogin = async () => {
    if (!code || code.length < 4) {
      setError('请输入验证码')
      return
    }
    setError('')
    setLoading(true)
    try {
      // TODO: bridge.call('auth.login', { phone, code })
      navigate('/home')
    } catch {
      setError('登录失败，请重试')
    } finally {
      setLoading(false)
    }
  }

  const handleGuestLogin = async () => {
    setLoading(true)
    try {
      // TODO: bridge.call('auth.guest', {})
      navigate('/home')
    } catch {
      setError('注册失败，请重试')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-6">
      <div className="max-w-sm w-full">
        {/* 标题 */}
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold mb-1" style={{ color: 'var(--ink)' }}>登录</h1>
          <p className="text-sm text-ink2">登录后可跨设备同步数据</p>
        </div>

        {/* 表单 */}
        <div className="glass-card p-5 space-y-4">
          {step === 'phone' ? (
            <>
              <div>
                <label className="text-xs text-ink2 block mb-1">手机号</label>
                <input
                  type="tel"
                  value={phone}
                  onChange={e => setPhone(e.target.value.replace(/\D/g, '').slice(0, 11))}
                  placeholder="请输入手机号"
                  className="w-full px-3 py-2.5 rounded-lg text-sm outline-none"
                  style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
                  data-testid="phone-input"
                />
              </div>
              <button
                onClick={handleSendCode}
                disabled={loading || phone.length < 11}
                className="w-full py-2.5 rounded-xl text-white font-medium text-sm disabled:opacity-40"
                style={{ background: 'var(--accent)' }}
              >
                {loading ? '发送中...' : '获取验证码'}
              </button>
            </>
          ) : (
            <>
              <div className="text-xs text-ink2">验证码已发送至 {phone}</div>
              <div>
                <label className="text-xs text-ink2 block mb-1">验证码</label>
                <input
                  type="tel"
                  value={code}
                  onChange={e => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                  placeholder="请输入验证码"
                  className="w-full px-3 py-2.5 rounded-lg text-sm outline-none"
                  style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
                  data-testid="code-input"
                />
              </div>
              <button
                onClick={handleLogin}
                disabled={loading || code.length < 4}
                className="w-full py-2.5 rounded-xl text-white font-medium text-sm disabled:opacity-40"
                style={{ background: 'var(--accent)' }}
              >
                {loading ? '登录中...' : '登录'}
              </button>
              <button onClick={() => { setStep('phone'); setCode('') }}
                className="w-full text-xs text-ink3 text-center">
                更换手机号
              </button>
            </>
          )}

          {error && <div className="text-xs text-expense text-center">{error}</div>}
        </div>

        {/* 游客入口 */}
        <div className="mt-6 text-center">
          <button
            onClick={handleGuestLogin}
            disabled={loading}
            className="text-sm text-ink3 underline"
            data-testid="guest-login-btn"
          >
            不登录，先体验一下
          </button>
        </div>

        {/* 返回 */}
        <div className="mt-4 text-center">
          <button onClick={() => navigate(-1)} className="text-xs text-ink3">
            ← 返回
          </button>
        </div>
      </div>
    </div>
  )
}
