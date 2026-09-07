/**
 * MePage — "我的"页。
 *
 * 账户、账本、分类、预算、主题切换、提醒/导出占位。
 * T-M5-04c: 新增"删除账号"按钮与二次确认对话框。
 */

import { useState } from 'react'
import { useTheme, type ThemeName } from '../../design/theme'
import { PageHeader } from '../../components/Layout'
import { useNavigate } from 'react-router'
import { NativeBridge, BridgeError } from '../../platform/bridge/native-bridge'

const themeOptions: { value: ThemeName; label: string; icon: string }[] = [
  { value: 'dawn', label: '晨', icon: '🌅' },
  { value: 'day', label: '昼', icon: '☀️' },
  { value: 'dusk', label: '暮', icon: '🌇' },
  { value: 'night', label: '夜', icon: '🌙' },
]

const modeOptions: { value: 'auto' | 'system' | 'manual'; label: string }[] = [
  { value: 'auto', label: '自动（跟随时间）' },
  { value: 'system', label: '跟随系统' },
  { value: 'manual', label: '手动选择' },
]

export function MePage() {
  const { theme, mode, setAuto, setSystem, setManual } = useTheme()
  const navigate = useNavigate()
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)
  const [deleteConfirmation, setDeleteConfirmation] = useState('')
  const [deleteInProgress, setDeleteInProgress] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const bridge = new NativeBridge()

  const handleDeleteAccount = async () => {
    if (deleteConfirmation !== 'DELETE') return
    setDeleteInProgress(true)
    setDeleteError(null)
    try {
      // 1. 获取一次性 confirm_token
      const { confirmToken } = await bridge.requestAccountDeletionToken()
      // 2. 消费 token，服务端将账号置为 frozen
      await bridge.deleteAccount(confirmToken)
      // 成功后 Kotlin 侧已清空本地状态并触发 /onboarding 导航
    } catch (e) {
      const message =
        e instanceof BridgeError
          ? e.code === 'invalid_token'
            ? '确认码已失效，请重新操作'
            : e.code === 'deletion_pending'
            ? '账号已在删除流程中'
            : e.code === 'auth_lost'
            ? '登录已失效，请重新登录'
            : e.message || '删除失败'
          : '删除失败，请稍后重试'
      setDeleteError(message)
      setDeleteInProgress(false)
    }
  }

  return (
    <div className="min-h-screen">
      <PageHeader title="我的" />

      <div className="px-4 py-4 space-y-4 pb-20">
        {/* 用户信息 */}
        <div className="glass-card p-4 flex items-center gap-4">
          <div className="w-12 h-12 rounded-full flex items-center justify-center text-2xl"
            style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}>
            👤
          </div>
          <div>
            <div className="text-base font-medium" style={{ color: 'var(--ink)' }}>Aozora 用户</div>
            <div className="text-xs text-ink3">游客模式</div>
          </div>
        </div>

        {/* 数据管理 */}
        <div className="glass-card overflow-hidden">
          <MenuSection title="数据管理">
            <MenuItem icon="💰" label="账户" />
            <MenuItem icon="📒" label="账本" />
            <MenuItem icon="📂" label="分类" />
            <MenuItem icon="📊" label="预算" />
          </MenuSection>
        </div>

        {/* 主题设置 */}
        <div className="glass-card p-4">
          <h3 className="text-sm font-medium mb-3" style={{ color: 'var(--ink)' }}>主题</h3>

          {/* 模式选择 */}
          <div className="space-y-2 mb-3">
            {modeOptions.map(opt => (
              <button
                key={opt.value}
                onClick={() => {
                  if (opt.value === 'auto') setAuto()
                  else if (opt.value === 'system') setSystem()
                  else if (opt.value === 'manual') setManual(theme)
                }}
                className="w-full text-left px-3 py-2 rounded-lg text-sm transition-colors flex items-center justify-between"
                style={{
                  background: mode === opt.value ? 'var(--glass)' : 'transparent',
                  color: mode === opt.value ? 'var(--accent)' : 'var(--ink2)',
                }}
              >
                {opt.label}
                {mode === opt.value && <span>✓</span>}
              </button>
            ))}
          </div>

          {/* 手动主题选择 */}
          <div className="grid grid-cols-4 gap-2">
            {themeOptions.map(opt => (
              <button
                key={opt.value}
                onClick={() => setManual(opt.value)}
                className="flex flex-col items-center gap-1 py-2 rounded-lg transition-colors"
                style={{
                  background: mode === 'manual' && theme === opt.value ? 'var(--glass)' : 'transparent',
                }}
              >
                <span className="text-xl">{opt.icon}</span>
                <span className="text-xs text-ink2">{opt.label}</span>
              </button>
            ))}
          </div>
        </div>

        {/* 功能入口（占位） */}
        <div className="glass-card overflow-hidden">
          <MenuSection title="更多">
            <MenuItem icon="⏰" label="提醒设置" onClick={() => navigate('/settings/reminder')} />
            <MenuItem icon="📤" label="数据导出" onClick={() => navigate('/settings/export')} />
            <MenuItem icon="🔐" label="登录与安全" badge="即将上线" />
            <MenuItem icon="ℹ️" label="关于" />
          </MenuSection>
        </div>

        {/* T-M5-04c: 危险区 — 账号删除 */}
        <div className="glass-card overflow-hidden">
          <MenuSection title="危险区">
            <MenuItem
              icon="⚠️"
              label="删除账号"
              onClick={() => {
                setDeleteConfirmation('')
                setDeleteError(null)
                setShowDeleteDialog(true)
              }}
            />
          </MenuSection>
        </div>

        {/* 版本 */}
        <div className="text-center text-xs text-ink3 py-4">
          Aozora v0.1.0
        </div>

        {/* T-M5-04c: 删除账号二次确认对话框 */}
        {showDeleteDialog && (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
            style={{ background: 'rgba(0, 0, 0, 0.5)' }}
            onClick={() => !deleteInProgress && setShowDeleteDialog(false)}
          >
            <div
              className="glass-card p-6 w-full max-w-sm space-y-4"
              style={{ border: '1px solid var(--glass-border)' }}
              onClick={e => e.stopPropagation()}
            >
              <div className="text-center space-y-2">
                <div className="text-3xl">⚠️</div>
                <h3 className="text-base font-semibold" style={{ color: 'var(--ink)' }}>
                  删除账号
                </h3>
                <p className="text-xs text-ink2 leading-relaxed">
                  此操作不可撤销。确认后账号将立即冻结，7 天内未取消则永久删除所有数据（流水、账本、分类、附件等）。
                </p>
              </div>
              <div className="space-y-2">
                <label className="text-xs text-ink2">
                  请输入 <code className="font-mono font-bold" style={{ color: 'var(--accent)' }}>DELETE</code> 以确认：
                </label>
                <input
                  type="text"
                  value={deleteConfirmation}
                  onChange={e => setDeleteConfirmation(e.target.value)}
                  placeholder="DELETE"
                  disabled={deleteInProgress}
                  className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                  style={{
                    background: 'var(--glass)',
                    color: 'var(--ink)',
                    border: '1px solid var(--glass-border)',
                  }}
                  autoFocus
                />
              </div>
              {deleteError && (
                <div
                  className="text-xs px-3 py-2 rounded-lg"
                  style={{ background: 'rgba(220, 38, 38, 0.1)', color: '#dc2626' }}
                >
                  {deleteError}
                </div>
              )}
              <div className="flex gap-2">
                <button
                  onClick={() => setShowDeleteDialog(false)}
                  disabled={deleteInProgress}
                  className="flex-1 px-3 py-2 rounded-lg text-sm transition-colors"
                  style={{
                    background: 'var(--glass)',
                    color: 'var(--ink2)',
                  }}
                >
                  取消
                </button>
                <button
                  onClick={handleDeleteAccount}
                  disabled={deleteConfirmation !== 'DELETE' || deleteInProgress}
                  className="flex-1 px-3 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-40"
                  style={{
                    background: deleteConfirmation === 'DELETE' ? '#dc2626' : 'var(--glass)',
                    color: deleteConfirmation === 'DELETE' ? '#fff' : 'var(--ink3)',
                  }}
                >
                  {deleteInProgress ? '处理中…' : '确认删除'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function MenuSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="px-4 py-2 text-xs text-ink3 font-medium">{title}</div>
      {children}
    </div>
  )
}

function MenuItem({ icon, label, badge, onClick }: { icon: string; label: string; badge?: string; onClick?: () => void }) {
  return (
    <button onClick={onClick} className="w-full flex items-center gap-3 px-4 py-3 row-line text-left transition-colors hover:opacity-80">
      <span className="text-lg">{icon}</span>
      <span className="text-sm flex-1" style={{ color: 'var(--ink)' }}>{label}</span>
      {badge && <span className="text-[10px] px-2 py-0.5 rounded-full" style={{ background: 'var(--glass)', color: 'var(--ink3)' }}>{badge}</span>}
      <span className="text-ink3 text-xs">›</span>
    </button>
  )
}
