/**
 * LoginPage — 登录页。
 *
 * FIX-004 / BUG-001: 接线到 auth.getStatus + triggerSync。
 * 第十轮 T10-3（REQ-19）: 手机号 + 验证码登录接真桥（auth.login）；
 * dev 环境验证码为服务端固定码 dev_auth_code（默认 000000），SMS 供应商仍待 D-EXEC-03。
 */

import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router'
import { BridgeError } from '../../platform/bridge/native-bridge'
import { useBridge } from '../../platform/hooks/use-bridge'

export function LoginPage() {
  const navigate = useNavigate()
  const bridge = useBridge()
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [step, setStep] = useState<'phone' | 'code'>('phone')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [authStatus, setAuthStatus] = useState<{
    registered: boolean
    userId: string | null
  } | null>(null)

  // FIX-004: 加载当前认证状态
  useEffect(() => {
    bridge.getAuthStatus().then(setAuthStatus).catch(() => {})
  }, [bridge])

  const handleSendCode = async () => {
    if (!phone || phone.length < 11) {
      setError('请输入正确的手机号')
      return
    }
    setError('')
    setLoading(true)
    try {
      // 手机验证码登录待 D-EXEC-03 SMS 供应商确认
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
      // 第十轮 T10-3（决策 #4）：真调 auth.login → 成功后同步一轮
      // （新账号云端为空 → bootstrap 清本地重建，与 UI 提示一致）
      await bridge.loginAuth(phone, code)
      try {
        await bridge.triggerAuthSync()
      } catch { /* 同步失败不阻断跳转，下次进首页会再触发 */ }
      navigate('/home', { replace: true })
    } catch (e) {
      const message =
        e instanceof BridgeError
          ? e.code === 'invalid_code'
            ? '验证码不正确或已过期，请重试'
            : e.code === 'auth_lost'
            ? '登录状态异常，请重试'
            : e.code === 'network_error'
            ? '网络错误，请检查网络连接后重试'
            : e.code === 'server_rejected'
            ? '登录被拒绝，请稍后重试'
            : e.code === 'invalid_input'
            ? '手机号或验证码格式不正确'
            : e.message || '登录失败，请重试'
          : '登录失败，请重试'
      setError(message)
    } finally {
      setLoading(false)
    }
  }

  const handleSyncNow = async () => {
    setLoading(true)
    setError('')
    try {
      await bridge.triggerAuthSync()
      const status = await bridge.getAuthStatus()
      setAuthStatus(status)
      if (status.registered) {
        navigate('/home')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '同步失败，请检查网络')
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
          {/* dev 演示环境固定验证码（server config dev_auth_code，默认 000000；
              SMS 供应商接入前一直有效 — D-EXEC-03） */}
          <p className="text-xs text-ink3 mt-2" data-testid="dev-code-hint">演示环境验证码：000000</p>
        </div>

        {/* FIX-004: 认证状态卡片 */}
        {authStatus && (
          <div className="glass-card p-4 mb-4">
            <div className="text-xs text-ink3 mb-1">当前状态</div>
            <div className="text-sm" style={{ color: 'var(--ink)' }}>
              {authStatus.registered
                ? `已注册（用户 ${authStatus.userId?.slice(0, 8) || '未知'}…）`
                : '游客模式（未注册）'}
            </div>
            {!authStatus.registered && (
              <button
                onClick={handleSyncNow}
                disabled={loading}
                className="mt-3 w-full py-2.5 rounded-xl text-white font-medium text-sm disabled:opacity-40"
                style={{ background: 'var(--accent)' }}
              >
                {loading ? '同步中...' : '立即同步并注册'}
              </button>
            )}
          </div>
        )}

        {/* 表单（手机号登录占位 — 待 D-EXEC-03） */}
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
