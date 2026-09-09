/**
 * UserSettingsPage — 用户设定（第十一轮 T11-6 / REQ-27 / 决策 #7）。
 *
 * 用户画像编辑页：称呼/职业/生活节奏/固定支出提示/消费偏好/沟通风格/其他，
 * 统一存 meta `user_profile`（JSON 对象 ≤8KB，仅本地不上行）。
 * 保存 = 单按钮 → meta.set + dirty 提示 + 成功轻提示（区别于组件页的即改即存——
 * 文本画像字段多，逐字段写盘会打字一个字符写一次，统一提交更合理）。
 * 恢复 = meta.get 解析回填；坏 JSON/非对象回退空表单。
 * 消费端：AssistantPage 读同键解析后注入 ai.interpret context.profile（在线 AI 路径）。
 */

import { useCallback, useEffect, useState } from 'react'
import { PageHeader } from '../../components/Layout'
import { useBridge } from '../../platform/hooks/use-bridge'

const META_KEY_USER_PROFILE = 'user_profile'
/** 与 native MetaWhitelist.MAX_VALUE_LEN 同限（超限时桥会拒，前端先行给可读错误）。 */
const MAX_VALUE_LEN = 8192

interface ProfileForm {
  nickname: string             // 如何称呼你（≤32）
  occupation: string           // 职业（≤32）
  lifeRhythm: string           // 生活节奏（≤300）
  fixedExpenses: string        // 固定支出提示（≤300）
  spendingPreference: string   // 消费偏好（≤300）
  communicationStyle: string   // 沟通风格（chips 单选值，可空）
  extra: string                // 其他想让你知道的（≤500）
}

const EMPTY_FORM: ProfileForm = {
  nickname: '',
  occupation: '',
  lifeRhythm: '',
  fixedExpenses: '',
  spendingPreference: '',
  communicationStyle: '',
  extra: '',
}

/** 写盘键序固定（避免序列化抖动，测试可断言 payload 形状）。 */
const FIELD_ORDER: (keyof ProfileForm)[] = [
  'nickname', 'occupation', 'lifeRhythm', 'fixedExpenses',
  'spendingPreference', 'communicationStyle', 'extra',
]

const COMM_STYLES = ['简洁直接', '温和鼓励', '幽默轻松', '严谨正式'] as const

/** 只保留非空字段；全空 = '{}'（服务端渲染空段落 → 行为等同无 context）。 */
function serializeForm(form: ProfileForm): string {
  const out: Record<string, string> = {}
  for (const k of FIELD_ORDER) {
    const v = form[k].trim()
    if (v) out[k] = v
  }
  return JSON.stringify(out)
}

/** 读取端容错：坏 JSON/非对象/数组 → 空表单；未知键忽略（本页是该键唯一权威写方）。 */
function parseStoredForm(raw: string | null | undefined): ProfileForm {
  const form = { ...EMPTY_FORM }
  if (!raw) return form
  try {
    const v: unknown = JSON.parse(raw)
    if (typeof v !== 'object' || v === null || Array.isArray(v)) return form
    const o = v as Record<string, unknown>
    for (const k of FIELD_ORDER) {
      if (typeof o[k] === 'string') form[k] = o[k] as string
    }
    return form
  } catch {
    return form
  }
}

export function UserSettingsPage() {
  const bridge = useBridge()
  const [form, setForm] = useState<ProfileForm>({ ...EMPTY_FORM })
  // 已存盘快照（恢复回填后同步）——dirty = 当前序列化与之不等。
  const [savedJson, setSavedJson] = useState('{}')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [savedTip, setSavedTip] = useState(false)

  // 恢复：meta.get 解析回填（读失败静默保持空表单，与组件页同语义）
  useEffect(() => {
    let cancelled = false
    bridge
      .getMeta([META_KEY_USER_PROFILE])
      .then((m) => {
        if (cancelled) return
        const parsed = parseStoredForm(m[META_KEY_USER_PROFILE])
        setForm(parsed)
        setSavedJson(serializeForm(parsed))
      })
      .catch(() => { /* 桥不可用 → 空表单 */ })
    return () => { cancelled = true }
  }, [bridge])

  // 成功轻提示 2s 自动消失
  useEffect(() => {
    if (!savedTip) return
    const t = setTimeout(() => setSavedTip(false), 2000)
    return () => clearTimeout(t)
  }, [savedTip])

  const currentJson = serializeForm(form)
  const dirty = currentJson !== savedJson

  const change = (patch: Partial<ProfileForm>) => setForm(prev => ({ ...prev, ...patch }))

  const handleSave = useCallback(async () => {
    if (saving) return
    if (currentJson.length > MAX_VALUE_LEN) {
      setError('内容总长超限，请精简后再保存')
      return
    }
    setSaving(true)
    try {
      await bridge.setMeta(META_KEY_USER_PROFILE, currentJson)
      setSavedJson(currentJson)
      setError(null)
      setSavedTip(true)
    } catch {
      setError('保存失败，请重试')
    } finally {
      setSaving(false)
    }
  }, [bridge, currentJson, saving])

  return (
    <div className="min-h-screen">
      <PageHeader back />

      <div className="px-4 py-4 space-y-4 pb-28">
        <div className="glass-card p-4 text-xs text-ink2 leading-relaxed">
          这些信息仅保存在本机，用于让 AI 助手的回答更贴近你的习惯与语气。
        </div>

        <div className="glass-card p-4 space-y-4">
          <Field
            testId="user-pref-nickname"
            label="如何称呼你"
            placeholder="如：老板 / 小岛"
            maxLength={32}
            value={form.nickname}
            onChange={v => change({ nickname: v })}
          />
          <Field
            testId="user-pref-occupation"
            label="职业"
            placeholder="如：自由职业"
            maxLength={32}
            value={form.occupation}
            onChange={v => change({ occupation: v })}
          />
          <Field
            testId="user-pref-life-rhythm"
            label="生活节奏"
            placeholder="如：经常加班，晚饭点不定；周末喜欢户外活动"
            maxLength={300}
            multiline
            value={form.lifeRhythm}
            onChange={v => change({ lifeRhythm: v })}
          />
          <Field
            testId="user-pref-fixed-expenses"
            label="固定支出提示"
            placeholder="如：每月 1 日房租、15 日健身会费"
            maxLength={300}
            multiline
            value={form.fixedExpenses}
            onChange={v => change({ fixedExpenses: v })}
          />
          <Field
            testId="user-pref-spending-preference"
            label="消费偏好"
            placeholder="如：吃饭重品质不重价格，交通优先地铁"
            maxLength={300}
            multiline
            value={form.spendingPreference}
            onChange={v => change({ spendingPreference: v })}
          />

          {/* 沟通风格：单选 chips，再点一次取消选择 */}
          <div>
            <div className="text-sm mb-2" style={{ color: 'var(--ink)' }}>沟通风格</div>
            <div className="flex flex-wrap gap-2">
              {COMM_STYLES.map(s => {
                const active = form.communicationStyle === s
                return (
                  <button
                    key={s}
                    type="button"
                    data-testid={`user-pref-style-${s}`}
                    aria-pressed={active}
                    onClick={() => change({ communicationStyle: active ? '' : s })}
                    className="text-xs px-3 py-1.5 rounded-full transition-colors"
                    style={{
                      background: active ? 'var(--accent)' : 'var(--glass)',
                      color: active ? 'white' : 'var(--ink2)',
                      border: `1px solid ${active ? 'var(--accent)' : 'var(--glass-border)'}`,
                    }}
                  >
                    {s}
                  </button>
                )
              })}
            </div>
          </div>

          <Field
            testId="user-pref-extra"
            label="其他想让你知道的"
            placeholder="任何想让 AI 助手了解的信息"
            maxLength={500}
            multiline
            value={form.extra}
            onChange={v => change({ extra: v })}
          />
        </div>

        {error && (
          <div
            data-testid="user-pref-error"
            className="text-xs px-3 py-2 rounded-lg"
            style={{ background: 'rgba(220, 38, 38, 0.1)', color: '#dc2626' }}
          >
            {error}
          </div>
        )}
        {savedTip && (
          <div
            data-testid="user-pref-saved"
            className="text-xs px-3 py-2 rounded-lg"
            style={{ background: 'rgba(22, 163, 74, 0.1)', color: '#16a34a' }}
          >
            ✓ 已保存
          </div>
        )}
      </div>

      {/* 底部保存条：dirty 提示 + 统一保存按钮 */}
      <div
        className="fixed bottom-0 left-0 right-0 px-4 py-3 flex items-center gap-3"
        style={{
          background: 'var(--nav-bg)',
          borderTop: '1px solid var(--glass-border)',
          paddingBottom: 'calc(env(safe-area-inset-bottom, 0px) + 12px)',
        }}
      >
        <div className="flex-1 text-xs" style={{ color: 'var(--ink3)' }} data-testid="user-pref-dirty">
          {dirty ? '有未保存的修改' : savedJson !== '{}' ? '已保存' : '尚未设定'}
        </div>
        <button
          type="button"
          data-testid="user-pref-save"
          onClick={() => void handleSave()}
          disabled={!dirty || saving}
          className="px-6 py-2 rounded-xl text-sm font-medium text-white transition-opacity disabled:opacity-40"
          style={{ background: 'var(--accent)' }}
        >
          {saving ? '保存中…' : '保存'}
        </button>
      </div>
    </div>
  )
}

function Field({
  testId,
  label,
  placeholder,
  maxLength,
  value,
  onChange,
  multiline,
}: {
  testId: string
  label: string
  placeholder: string
  maxLength: number
  value: string
  onChange: (v: string) => void
  multiline?: boolean
}) {
  const cls = 'w-full px-3 py-2 rounded-lg text-sm outline-none'
  const style = {
    background: 'var(--glass)',
    color: 'var(--ink)',
    border: '1px solid var(--glass-border)',
  }
  return (
    <label className="block">
      <div className="text-sm mb-1.5" style={{ color: 'var(--ink)' }}>{label}</div>
      {multiline ? (
        <textarea
          data-testid={testId}
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder={placeholder}
          maxLength={maxLength}
          rows={3}
          className={cls}
          style={style}
        />
      ) : (
        <input
          type="text"
          data-testid={testId}
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder={placeholder}
          maxLength={maxLength}
          className={cls}
          style={style}
        />
      )}
    </label>
  )
}
