/**
 * SecurityPage — 登录与安全（第十轮 T10-3 / REQ-19 + REQ-20 上半）。
 *
 * 游客/正式账号体系统一落点（决策 #3）：身份卡 + 修改昵称 + 修改头像 +
 * 登录/升级账号 + 切换账号 + 退出登录。
 * 昵称/头像存 sync_meta 本地键（决策 #4，换机不保留）；
 * 退出登录 = 远端吊销会话 + 清本地业务数据（云端保留，重新登录可拉回）。
 */

import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { PageHeader } from '../../components/Layout'
import { BridgeError } from '../../platform/bridge/native-bridge'
import { useBridge } from '../../platform/hooks/use-bridge'

export function SecurityPage() {
  const bridge = useBridge()
  const navigate = useNavigate()

  // ─── 身份状态（getStatus + meta + me 内存缓存） ───
  const [registered, setRegistered] = useState(false)
  const [userId, setUserId] = useState<string | null>(null)
  const [displayName, setDisplayName] = useState<string | null>(null)
  const [avatarUri, setAvatarUri] = useState<string | null>(null)
  const [phoneMasked, setPhoneMasked] = useState<string | null>(null)

  // ─── 弹层/操作状态 ───
  const [editingNick, setEditingNick] = useState(false)
  const [nickDraft, setNickDraft] = useState('')
  const [nickSaving, setNickSaving] = useState(false)
  // REQ-31：昵称错误必须渲染在弹层内——pageError 在弹层遮罩之外，用户看不见
  const [nickError, setNickError] = useState<string | null>(null)
  const [avatarBusy, setAvatarBusy] = useState(false)
  const [confirmMode, setConfirmMode] = useState<'logout' | 'switch' | null>(null)
  const [busy, setBusy] = useState(false)
  const [pageError, setPageError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [status, meta] = await Promise.all([
        bridge.getAuthStatus(),
        bridge.getMeta(['display_name', 'avatar_uri']).catch(() => ({}) as Record<string, string | null>),
      ])
      setRegistered(status.registered)
      setUserId(status.userId)
      setDisplayName(meta['display_name'] || null)
      setAvatarUri(meta['avatar_uri'] || null)
      if (status.registered) {
        // 副行 = 脱敏手机号；失败（离线/auth_lost）静默，回退 userId 前 8 位展示
        try {
          const me = await bridge.getMe()
          if (me.phone) setPhoneMasked(`${me.phone.slice(0, 3)}****${me.phone.slice(-4)}`)
        } catch { /* 兜底走 userId */ }
      }
    } catch { /* 桥不可用时保留静态兜底 */ }
  }, [bridge])

  useEffect(() => { load() }, [load])

  const nickname = displayName ?? (registered ? 'Aozora 用户' : '游客模式')
  const subLine = registered
    ? (phoneMasked ?? `用户 ${userId?.slice(0, 8) ?? '未知'}…`)
    : '游客模式 · 未登录'

  // ─── 修改昵称（决策 #4：PATCH /me + native 镜像写 display_name） ───

  const openNickEditor = () => {
    setNickDraft(displayName ?? '')
    setPageError(null)
    setNickError(null)
    setEditingNick(true)
  }

  const saveNickname = async () => {
    const v = nickDraft.trim()
    if (v.length < 1 || v.length > 64) {
      setNickError('昵称需为 1–64 个字符')
      return
    }
    setNickSaving(true)
    setNickError(null)
    try {
      const me = await bridge.updateProfile(v)
      setDisplayName(me.nickname ?? v)
      setEditingNick(false)
    } catch (e) {
      setNickError(authErrorMessage(e, '昵称保存失败，请稍后重试'))
    } finally {
      setNickSaving(false)
    }
  }

  // ─── 修改头像（icon.pick 压缩链 → meta avatar_uri，仅本地） ───

  const pickAvatar = async () => {
    setAvatarBusy(true)
    setPageError(null)
    try {
      const r = await bridge.pickIcon('gallery')
      await bridge.setMeta('avatar_uri', r.dataUri)
      setAvatarUri(r.dataUri)
    } catch (e) {
      // 用户取消静默（与 IconPicker 先例一致）
      if (!(e instanceof BridgeError && e.code === 'cancelled')) {
        setPageError(e instanceof BridgeError && e.code === 'unavailable'
          ? '当前环境不支持选择照片'
          : '头像设置失败，请重试')
      }
    } finally {
      setAvatarBusy(false)
    }
  }

  // ─── 退出登录 / 切换账号（决策 #4：logout 清本地，云端保留） ───

  const doLogout = async (after: 'onboarding' | 'login') => {
    setBusy(true)
    setPageError(null)
    try {
      await bridge.logoutAuth()
      setConfirmMode(null)
      if (after === 'onboarding') {
        navigate('/onboarding', { replace: true })
      } else {
        navigate('/login')
      }
    } catch (e) {
      const message =
        e instanceof BridgeError && e.code === 'wipe_failed'
          ? '退出已完成，但本机数据清理失败，请重试退出'
          : '退出失败，请稍后重试'
      setPageError(message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen">
      <PageHeader back />

      <div className="px-4 py-4 space-y-4 pb-20">
        {/* ─── 身份卡 ─── */}
        <div className="glass-card p-4 flex items-center gap-4" data-testid="security-identity">
          {avatarUri ? (
            <img
              src={avatarUri}
              alt="头像"
              className="w-14 h-14 rounded-full object-cover shrink-0"
              style={{ border: '1px solid var(--glass-border)' }}
            />
          ) : (
            <div
              className="w-14 h-14 rounded-full flex items-center justify-center text-2xl shrink-0"
              style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
            >
              👤
            </div>
          )}
          <div className="min-w-0">
            <div className="text-base font-medium truncate" style={{ color: 'var(--ink)' }}>
              {nickname}
            </div>
            <div className="text-xs text-ink3">{subLine}</div>
          </div>
        </div>

        {/* ─── 账号操作 ─── */}
        <div className="glass-card overflow-hidden">
          <div className="px-4 py-2 text-xs text-ink3 font-medium">账号</div>
          <SettingRow
            icon="✏️"
            label="修改昵称"
            onClick={openNickEditor}
            testId="security-nickname-button"
          />
          <SettingRow
            icon="🖼️"
            label="修改头像"
            hint="仅保存在本机"
            onClick={pickAvatar}
            disabled={avatarBusy}
            testId="security-avatar-button"
          />
          {!registered ? (
            <SettingRow
              icon="📱"
              label="登录 / 升级账号"
              onClick={() => navigate('/login')}
              testId="security-login-button"
            />
          ) : (
            <>
              <SettingRow
                icon="🔄"
                label="切换账号"
                onClick={() => { setPageError(null); setConfirmMode('switch') }}
                testId="security-switch-account"
              />
              <SettingRow
                icon="🚪"
                label="退出登录"
                danger
                onClick={() => { setPageError(null); setConfirmMode('logout') }}
                testId="security-logout"
              />
            </>
          )}
        </div>

        {/* 游客登录提示（决策 #4：登录会切换云端身份，本地数据由同步重建） */}
        {!registered && (
          <p className="text-xs text-ink3 leading-relaxed px-1">
            登录后即可跨设备同步。若登录全新手机号账号，本机数据将由云端数据重建。
          </p>
        )}

        {pageError && (
          <div
            className="text-xs px-3 py-2 rounded-lg"
            style={{ background: 'rgba(220, 38, 38, 0.1)', color: '#dc2626' }}
            data-testid="security-error"
            role="alert"
          >
            {pageError}
          </div>
        )}
      </div>

      {/* ─── 昵称编辑弹层 ─── */}
      {editingNick && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: 'rgba(0, 0, 0, 0.5)' }}
          onClick={() => !nickSaving && setEditingNick(false)}
        >
          <div
            className="glass-card p-5 w-full max-w-sm space-y-4"
            style={{ border: '1px solid var(--glass-border)' }}
            onClick={e => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold" style={{ color: 'var(--ink)' }}>
              修改昵称
            </h3>
            <input
              type="text"
              value={nickDraft}
              onChange={e => setNickDraft(e.target.value)}
              placeholder="1–64 个字符"
              maxLength={64}
              disabled={nickSaving}
              className="w-full px-3 py-2.5 rounded-lg text-sm outline-none"
              style={{
                background: 'var(--glass)',
                color: 'var(--ink)',
                border: '1px solid var(--glass-border)',
              }}
              data-testid="security-nickname-input"
              autoFocus
            />
            {nickError && (
              <div
                className="text-xs px-3 py-2 rounded-lg"
                style={{ background: 'rgba(220, 38, 38, 0.1)', color: '#dc2626' }}
                data-testid="security-nickname-error"
                role="alert"
              >
                {nickError}
              </div>
            )}
            <div className="flex gap-2">
              <button
                onClick={() => setEditingNick(false)}
                disabled={nickSaving}
                className="flex-1 px-3 py-2 rounded-lg text-sm"
                style={{ background: 'var(--glass)', color: 'var(--ink2)' }}
              >
                取消
              </button>
              <button
                onClick={saveNickname}
                disabled={nickSaving || nickDraft.trim().length === 0}
                className="flex-1 px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-40"
                style={{ background: 'var(--accent)', color: '#fff' }}
                data-testid="security-nickname-save"
              >
                {nickSaving ? '保存中…' : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ─── 退出/切换确认弹框（决策 #4：确认弹框必须写清数据去向） ─── */}
      {confirmMode && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: 'rgba(0, 0, 0, 0.5)' }}
          onClick={() => !busy && setConfirmMode(null)}
        >
          <div
            className="glass-card p-6 w-full max-w-sm space-y-4"
            style={{ border: '1px solid var(--glass-border)' }}
            onClick={e => e.stopPropagation()}
          >
            <div className="text-center space-y-2">
              <div className="text-3xl">{confirmMode === 'logout' ? '🚪' : '🔄'}</div>
              <h3 className="text-base font-semibold" style={{ color: 'var(--ink)' }}>
                {confirmMode === 'logout' ? '退出登录' : '切换账号'}
              </h3>
              <p className="text-xs text-ink2 leading-relaxed">
                退出将清除本机数据；未同步到云端的记录会丢失。
                <br />
                云端数据将保留，重新登录同一手机号可拉回。
                {confirmMode === 'switch' && '退出后将进入登录页，可更换手机号账号。'}
              </p>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => setConfirmMode(null)}
                disabled={busy}
                className="flex-1 px-3 py-2 rounded-lg text-sm"
                style={{ background: 'var(--glass)', color: 'var(--ink2)' }}
                data-testid="security-confirm-cancel"
              >
                取消
              </button>
              <button
                onClick={() => doLogout(confirmMode === 'logout' ? 'onboarding' : 'login')}
                disabled={busy}
                className="flex-1 px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-40"
                style={{ background: '#dc2626', color: '#fff' }}
                data-testid="security-confirm-ok"
              >
                {busy ? '处理中…' : (confirmMode === 'logout' ? '确认退出' : '继续切换')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function authErrorMessage(e: unknown, fallback: string): string {
  if (e instanceof BridgeError) {
    if (e.code === 'auth_lost') return '登录已失效，请重新登录'
    if (e.code === 'invalid_input') return '昵称格式不正确（1–64 个字符）'
    if (e.code === 'network_error') return '网络错误，请检查网络连接后重试'
    if (e.code === 'server_error') return '服务端暂时不可用，请稍后再试'
    return e.message || fallback
  }
  return fallback
}

function SettingRow({ icon, label, hint, danger, disabled, onClick, testId }: {
  icon: string
  label: string
  hint?: string
  danger?: boolean
  disabled?: boolean
  onClick: () => void
  testId: string
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="w-full flex items-center gap-3 px-4 py-3 row-line text-left transition-colors hover:opacity-80 disabled:opacity-40"
      data-testid={testId}
    >
      <span className="text-lg">{icon}</span>
      <span
        className="text-sm flex-1"
        style={{ color: danger ? '#dc2626' : 'var(--ink)' }}
      >
        {label}
      </span>
      {hint && <span className="text-[10px] text-ink3">{hint}</span>}
      <span className="text-ink3 text-xs">›</span>
    </button>
  )
}
