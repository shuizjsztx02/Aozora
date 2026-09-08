/**
 * FeedbackPage — T-M5-05c 应用内反馈表单。
 *
 * 用户提交 bug / suggestion / other 类型的反馈到 POST /telemetry/feedback。
 * 通过 NativeBridge → FeedbackClient → 服务端。
 */

import { useState } from 'react'
import { useNavigate } from 'react-router'
import { PageHeader } from '../../components/Layout'
import { BridgeError } from '../../platform/bridge/native-bridge'
import { useBridge } from '../../platform/hooks/use-bridge'
import type { FeedbackCategory } from '../../platform/bridge/bridge-types'

const categories: { value: FeedbackCategory; label: string; icon: string }[] = [
  { value: 'bug', label: 'Bug', icon: '🐛' },
  { value: 'suggestion', label: '建议', icon: '💡' },
  { value: 'other', label: '其他', icon: '💬' },
]

const CONTENT_MAX = 4000
const CONTACT_MAX = 128

export function FeedbackPage() {
  const navigate = useNavigate()
  // 使用全局 bridge 单例，避免多实例覆写全局回调导致请求挂起
  const bridge = useBridge()

  const [category, setCategory] = useState<FeedbackCategory>('bug')
  const [content, setContent] = useState('')
  const [contact, setContact] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [feedbackId, setFeedbackId] = useState<string | null>(null)

  const canSubmit = content.trim().length > 0 && content.length <= CONTENT_MAX && !submitting

  const handleSubmit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      const result = await bridge.submitFeedback({
        category,
        content: content.trim(),
        contact: contact.trim() || undefined,
      })
      setFeedbackId(result.id)
      setSuccess(true)
    } catch (e) {
      const message =
        e instanceof BridgeError
          ? e.code === 'rate_limited'
            ? '提交太频繁，请稍后再试（每小时最多 5 次）'
            : e.code === 'auth_lost'
            ? '登录已失效，请重新登录后再试'
            : e.code === 'network_error'
            ? '网络错误，请检查网络连接后重试'
            : e.code === 'server_error'
            ? '服务端暂时不可用，请稍后再试'
            : e.code === 'invalid_input'
            ? '输入内容有误，请检查后重试'
            : e.code === 'timeout'
            ? '服务器 10 秒内未响应。请检查网络连接，或确认服务端 /telemetry/feedback 可达后重试'
            : e.message || '提交失败'
          : '提交失败，请稍后重试'
      setError(message)
    } finally {
      setSubmitting(false)
    }
  }

  if (success) {
    return (
      <div className="min-h-screen">
        <PageHeader back />
        <div className="px-4 py-8 flex flex-col items-center gap-4">
          <div className="text-4xl">✅</div>
          <h2 className="text-lg font-semibold" style={{ color: 'var(--ink)' }}>
            感谢你的反馈！
          </h2>
          <p className="text-sm text-ink2 text-center leading-relaxed max-w-xs">
            我们会认真阅读每一条反馈，帮助改进 Aozora。
          </p>
          {feedbackId && (
            <p className="text-xs text-ink3">反馈编号：{feedbackId}</p>
          )}
          <button
            onClick={() => navigate('/me', { replace: true })}
            className="mt-4 px-6 py-2 rounded-lg text-sm font-medium transition-colors"
            style={{ background: 'var(--accent)', color: '#fff' }}
          >
            返回
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen">
      <PageHeader back />

      <div className="px-4 py-4 space-y-4 pb-20">
        {/* 类别选择 */}
        <div className="glass-card p-4">
          <h3 className="text-sm font-medium mb-3" style={{ color: 'var(--ink)' }}>
            反馈类型
          </h3>
          <div className="flex gap-2">
            {categories.map(c => (
              <button
                key={c.value}
                onClick={() => setCategory(c.value)}
                className="flex-1 flex flex-col items-center gap-1 py-3 rounded-lg transition-colors"
                style={{
                  background: category === c.value ? 'var(--glass)' : 'transparent',
                  border: category === c.value ? '1px solid var(--accent)' : '1px solid var(--glass-border)',
                  color: category === c.value ? 'var(--accent)' : 'var(--ink2)',
                }}
              >
                <span className="text-xl">{c.icon}</span>
                <span className="text-xs">{c.label}</span>
              </button>
            ))}
          </div>
        </div>

        {/* 反馈内容 */}
        <div className="glass-card p-4">
          <h3 className="text-sm font-medium mb-2" style={{ color: 'var(--ink)' }}>
            详细描述
          </h3>
          <textarea
            value={content}
            onChange={e => setContent(e.target.value)}
            placeholder="请描述你遇到的问题或建议…&#10;&#10;• 发生了什么？&#10;• 你期望的行为是什么？&#10;• 如何重现？"
            maxLength={CONTENT_MAX}
            rows={6}
            className="w-full px-3 py-2 rounded-lg text-sm outline-none resize-none leading-relaxed"
            style={{
              background: 'var(--glass)',
              color: 'var(--ink)',
              border: '1px solid var(--glass-border)',
            }}
          />
          <div className="flex justify-between mt-1">
            <span className="text-[10px] text-ink3">请不要包含密码、账号等敏感信息</span>
            <span className={`text-[10px] ${content.length > CONTENT_MAX ? 'text-red-500' : 'text-ink3'}`}>
              {content.length}/{CONTENT_MAX}
            </span>
          </div>
        </div>

        {/* 联系方式（可选） */}
        <div className="glass-card p-4">
          <h3 className="text-sm font-medium mb-2" style={{ color: 'var(--ink)' }}>
            联系方式 <span className="text-ink3 font-normal">（可选）</span>
          </h3>
          <input
            type="text"
            value={contact}
            onChange={e => setContact(e.target.value.slice(0, CONTACT_MAX))}
            placeholder="邮箱或昵称，方便我们回复你"
            maxLength={CONTACT_MAX}
            className="w-full px-3 py-2 rounded-lg text-sm outline-none"
            style={{
              background: 'var(--glass)',
              color: 'var(--ink)',
              border: '1px solid var(--glass-border)',
            }}
          />
        </div>

        {/* 错误提示 */}
        {error && (
          <div
            className="glass-card px-4 py-3 text-sm"
            style={{ background: 'rgba(220, 38, 38, 0.08)', color: '#dc2626', border: '1px solid rgba(220, 38, 38, 0.2)' }}
          >
            {error}
          </div>
        )}

        {/* 提交按钮 */}
        <button
          onClick={handleSubmit}
          disabled={!canSubmit}
          className="w-full py-3 rounded-lg text-sm font-medium transition-colors disabled:opacity-40"
          style={{ background: 'var(--accent)', color: '#fff' }}
        >
          {submitting ? '提交中…' : '提交反馈'}
        </button>
      </div>
    </div>
  )
}
