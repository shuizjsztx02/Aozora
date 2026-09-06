/**
 * MePage — "我的"页。
 *
 * 账户、账本、分类、预算、主题切换、提醒/导出占位。
 */

import { useTheme, type ThemeName } from '../../design/theme'
import { PageHeader } from '../../components/Layout'
import { useNavigate } from 'react-router'

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
            <MenuItem icon="📤" label="数据导出" badge="即将上线" />
            <MenuItem icon="🔐" label="登录与安全" badge="即将上线" />
            <MenuItem icon="ℹ️" label="关于" />
          </MenuSection>
        </div>

        {/* 版本 */}
        <div className="text-center text-xs text-ink3 py-4">
          Aozora v0.1.0
        </div>
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
