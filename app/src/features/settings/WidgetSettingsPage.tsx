/**
 * WidgetSettingsPage — 桌面组件（第十一轮 T11-2 / REQ-24 / 决策 #5）。
 *
 * 应用内全局单配置（所有 widget 实例共享一份；每实例配置留 v2）：
 * 显示内容模块开关（今日支出恒显示，无开关；今日笔数/本月支出/预算进度可关）
 * + 点击行为（打开首页 / 直达记一笔）。
 * 保存即生效（决策 #5）：每次改动 meta.set('widget_config', JSON) + widget.refresh，
 * 对应验收"配置页关预算进度 → 桌面组件即时消失"。
 * 恢复 = meta.get 解析回填；缺键/坏 JSON 回退默认，与 native WidgetConfig.parse 对称。
 */

import { useCallback, useEffect, useState } from 'react'
import { PageHeader } from '../../components/Layout'
import { useBridge } from '../../platform/hooks/use-bridge'

const META_KEY_WIDGET_CONFIG = 'widget_config'

type TapAction = 'home' | 'entry'

interface WidgetConfigState {
  showTodayCount: boolean
  showMonthExpense: boolean
  showBudget: boolean
  tapAction: TapAction
}

const DEFAULT_CONFIG: WidgetConfigState = {
  showTodayCount: true,
  showMonthExpense: false,
  showBudget: true,
  tapAction: 'home',
}

/** 读取端容错：坏 JSON/非对象/缺键/坏值逐键回退默认（与 Kotlin WidgetConfig.parse 同语义）。 */
function parseConfig(raw: string | null | undefined): WidgetConfigState {
  if (!raw) return { ...DEFAULT_CONFIG }
  try {
    const v: unknown = JSON.parse(raw)
    if (typeof v !== 'object' || v === null) return { ...DEFAULT_CONFIG }
    const o = v as Partial<Record<keyof WidgetConfigState, unknown>>
    return {
      showTodayCount:
        typeof o.showTodayCount === 'boolean' ? o.showTodayCount : DEFAULT_CONFIG.showTodayCount,
      showMonthExpense:
        typeof o.showMonthExpense === 'boolean' ? o.showMonthExpense : DEFAULT_CONFIG.showMonthExpense,
      showBudget: typeof o.showBudget === 'boolean' ? o.showBudget : DEFAULT_CONFIG.showBudget,
      tapAction: o.tapAction === 'entry' ? 'entry' : 'home',
    }
  } catch {
    return { ...DEFAULT_CONFIG }
  }
}

/** 写盘统一形状（键序固定，避免重复序列化抖动）。 */
function serializeConfig(c: WidgetConfigState): string {
  return JSON.stringify({
    showTodayCount: c.showTodayCount,
    showMonthExpense: c.showMonthExpense,
    showBudget: c.showBudget,
    tapAction: c.tapAction,
  })
}

const tapOptions: { value: TapAction; label: string; desc: string }[] = [
  { value: 'home', label: '打开首页', desc: '点击进入 App 首页' },
  { value: 'entry', label: '直达记一笔', desc: '点击直接弹出快速记账' },
]

export function WidgetSettingsPage() {
  const bridge = useBridge()
  const [config, setConfig] = useState<WidgetConfigState>({ ...DEFAULT_CONFIG })
  const [error, setError] = useState<string | null>(null)

  // 恢复：读 meta 回填（读失败回退默认态，不阻塞页面）
  useEffect(() => {
    let cancelled = false
    bridge
      .getMeta([META_KEY_WIDGET_CONFIG])
      .then((m) => {
        if (!cancelled) setConfig(parseConfig(m[META_KEY_WIDGET_CONFIG]))
      })
      .catch(() => {
        /* 桥不可用 → 保持默认态 */
      })
    return () => {
      cancelled = true
    }
  }, [bridge])

  /** 保存并即时刷新桌面（决策 #5：保存 = meta.set + widget.refresh）。 */
  const persist = useCallback(
    async (next: WidgetConfigState) => {
      try {
        await bridge.setMeta(META_KEY_WIDGET_CONFIG, serializeConfig(next))
        await bridge.refreshWidget()
        setError(null)
      } catch {
        setError('保存失败，请重试')
      }
    },
    [bridge],
  )

  const change = (patch: Partial<WidgetConfigState>) => {
    const next = { ...config, ...patch }
    setConfig(next)
    void persist(next)
  }

  const refreshNow = async () => {
    try {
      await bridge.refreshWidget()
      setError(null)
    } catch {
      setError('刷新失败，请重试')
    }
  }

  return (
    <div className="min-h-screen">
      <PageHeader back />

      <div className="px-4 py-4 space-y-4 pb-20">
        <div className="glass-card p-4 text-xs text-ink2 leading-relaxed">
          桌面组件的长按替换/移除由系统桌面完成，本页配置显示内容。
        </div>

        {/* 显示内容 */}
        <div className="glass-card p-4">
          <div className="text-sm font-medium mb-3" style={{ color: 'var(--ink)' }}>
            显示内容
          </div>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm" style={{ color: 'var(--ink)' }}>今日支出</div>
                <div className="text-xs text-ink3 mt-0.5">金额（恒显示，不可关闭）</div>
              </div>
              <span
                className="text-[10px] px-2 py-0.5 rounded-full"
                style={{ background: 'var(--glass)', color: 'var(--ink3)' }}
              >
                常显
              </span>
            </div>

            <ConfigSwitch
              testId="widget-switch-today-count"
              label="今日笔数"
              desc="今日支出旁边显示笔数"
              checked={config.showTodayCount}
              onChange={(v) => change({ showTodayCount: v })}
            />
            <ConfigSwitch
              testId="widget-switch-month-expense"
              label="本月支出"
              desc="增加一行本月累计支出"
              checked={config.showMonthExpense}
              onChange={(v) => change({ showMonthExpense: v })}
            />
            <ConfigSwitch
              testId="widget-switch-budget"
              label="预算进度"
              desc="显示本月预算已用百分比"
              checked={config.showBudget}
              onChange={(v) => change({ showBudget: v })}
            />
          </div>
        </div>

        {/* 点击行为 */}
        <div className="glass-card p-4">
          <div className="text-sm font-medium mb-3" style={{ color: 'var(--ink)' }}>
            点击组件
          </div>
          <div className="space-y-2">
            {tapOptions.map((opt) => (
              <button
                key={opt.value}
                type="button"
                data-testid={`widget-tap-${opt.value}`}
                onClick={() => change({ tapAction: opt.value })}
                className="w-full text-left px-3 py-2 rounded-lg text-sm transition-colors flex items-center justify-between"
                style={{
                  background: config.tapAction === opt.value ? 'var(--glass)' : 'transparent',
                  color: config.tapAction === opt.value ? 'var(--accent)' : 'var(--ink2)',
                }}
              >
                <span>
                  {opt.label}
                  <span className="block text-xs text-ink3 mt-0.5">{opt.desc}</span>
                </span>
                {config.tapAction === opt.value && <span>✓</span>}
              </button>
            ))}
          </div>
        </div>

        {/* 立即刷新 */}
        <div className="glass-card p-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm" style={{ color: 'var(--ink)' }}>立即刷新桌面组件</div>
              <div className="text-xs text-ink3 mt-0.5">
                记账、同步后组件会自动更新；此处可手动触发一次
              </div>
            </div>
            <button
              type="button"
              data-testid="widget-refresh-button"
              onClick={refreshNow}
              className="text-xs px-3 py-1.5 rounded-lg"
              style={{ background: 'var(--accent)', color: 'white' }}
            >
              立即刷新
            </button>
          </div>
        </div>

        {error && (
          <div
            data-testid="widget-error"
            className="text-xs px-3 py-2 rounded-lg"
            style={{ background: 'rgba(220, 38, 38, 0.1)', color: '#dc2626' }}
          >
            {error}
          </div>
        )}
      </div>
    </div>
  )
}

function ConfigSwitch({
  testId,
  label,
  desc,
  checked,
  onChange,
}: {
  testId: string
  label: string
  desc: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <div className="flex items-center justify-between">
      <div>
        <div className="text-sm" style={{ color: 'var(--ink)' }}>{label}</div>
        <div className="text-xs text-ink3 mt-0.5">{desc}</div>
      </div>
      <label className="relative inline-flex items-center cursor-pointer">
        <input
          type="checkbox"
          data-testid={testId}
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
          className="sr-only peer"
        />
        <div
          className="w-11 h-6 rounded-full peer transition-colors"
          style={{ background: checked ? 'var(--accent)' : 'var(--glass-border)' }}
        >
          <div
            className="w-5 h-5 rounded-full bg-white shadow transition-transform mt-0.5"
            style={{ transform: checked ? 'translateX(22px)' : 'translateX(2px)' }}
          />
        </div>
      </label>
    </div>
  )
}
