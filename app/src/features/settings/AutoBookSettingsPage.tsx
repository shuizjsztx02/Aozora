/**
 * AutoBookSettingsPage — 自动记账设置页。
 *
 * 第二十一轮 T21-7（REQ-83）：「自动记账」开关 + 两类「设置页授权」各自的引导与状态
 * （无障碍服务 / 通知使用权）+ 内置来源清单 + 用途说明 + 撤销入口。
 *
 * 设计要点：
 * - 两类授权都是「系统设置页授权」（决策 #10），**不是运行时权限**——`checkSelfPermission`
 *   查不到、`requestPermission` 弹窗也走不到。只能跳系统页让用户手动授予。
 * - 三态（决策 #10）：UNAUTHORIZED / ENABLED / ENABLED_BUT_DISABLED，由后端
 *   `PermissionManager.getSettingsAuthStatus` 集中判定，前端按态渲染不同引导文案。
 * - 两授权**可各自独立开关**——只授一个也应有可用的一半能力。
 * - 文案红线（决策 #20）：不得写「100% 自动」或「绝不遗漏」——两条通道都有各自
 *   的失效面，对用户只能用「支付类通知到了、或在支付页上，就会自动记」。
 * - 来源清单第一期**只读展示内置三项**（微信 / 支付宝 / 云闪付），「从已安装应用
 *   添加」挂账后续轮次（桥命令 87 不动，T21-5 收口时的桥契约不含来源编辑命令）。
 *
 * 数据流：前端 → bridge → Kotlin PermissionManager / AutoBookRepository。
 */

import { useCallback, useEffect, useState } from 'react'
import { PageHeader } from '../../components/Layout'
import { useBridge } from '../../platform/hooks/use-bridge'
import type { SettingsAuthStatus } from '../../platform/bridge/bridge-types'

/** 内置三项来源（T21-3 `NotificationFilter.DEFAULT_SOURCE_PACKAGES` 同源）。 */
const BUILT_IN_SOURCES: { pkg: string; label: string; icon: string }[] = [
  { pkg: 'com.tencent.mm', label: '微信', icon: '💬' },
  { pkg: 'com.eg.android.AlipayGphone', label: '支付宝', icon: '🔵' },
  { pkg: 'com.unionpay.tsmservice', label: '云闪付', icon: '💳' },
]

type Target = 'accessibility' | 'notification_listener'

export function AutoBookSettingsPage() {
  const bridge = useBridge()

  const [enabled, setEnabled] = useState(false)
  const [accessibilityStatus, setAccessibilityStatus] = useState<SettingsAuthStatus>('unauthorized')
  const [notificationStatus, setNotificationStatus] = useState<SettingsAuthStatus>('unauthorized')
  const [todayCount, setTodayCount] = useState(0)
  const [pendingCount, setPendingCount] = useState(0)
  // 「刚从系统设置页返回」的刷新触发器：`visibilitychange` / `resume` 都会 +1。
  const [refreshTick, setRefreshTick] = useState(0)

  const loadStatus = useCallback(async () => {
    try {
      const s = await bridge.getAutoBookStatus()
      setEnabled(s.enabled)
      setAccessibilityStatus(s.accessibilityStatus)
      setNotificationStatus(s.notificationStatus)
      setTodayCount(s.todayCaptureCount)
      setPendingCount(s.pendingConfirmCount)
    } catch {
      /* 桥不可用时保持默认值；UI 不会崩溃 */
    }
  }, [bridge])

  useEffect(() => {
    loadStatus()
  }, [loadStatus, refreshTick])

  // 用户跳系统设置页再返回时，Activity 走 onResume；WebView 触发 visibilitychange。
  // 此时重新拉一次状态——三态可能从 unauthorized → enabled，或 enabled → disabled。
  useEffect(() => {
    const handler = () => {
      if (document.visibilityState === 'visible') setRefreshTick(t => t + 1)
    }
    document.addEventListener('visibilitychange', handler)
    return () => document.removeEventListener('visibilitychange', handler)
  }, [])

  const handleToggle = async (value: boolean) => {
    // 任何一个授权都未授予时，不允许打开开关——否则开关开了也不会有任何数据进来。
    // UI 上此时开关本身已 disabled，但这里再守一次以防竞态。
    if (value && accessibilityStatus === 'unauthorized' && notificationStatus === 'unauthorized') {
      return
    }
    setEnabled(value)
    try {
      await bridge.setAutoBookEnabled(value)
    } catch {
      setEnabled(!value)
    }
  }

  const handleOpenSystemSettings = async (target: Target) => {
    try {
      await bridge.openPermissionSettings(target)
      // 跳系统页后状态变更要等用户返回时才看得见；此处不立即刷新（无意义）。
    } catch {
      /* 跳系统页失败不阻塞 UI */
    }
  }

  // 决策 #10：两授权各自独立；只要至少一个已启用（即便另一个停用或未授），开关就可用。
  // 「已授权但停用」也算作"有能力工作"——用户只是当前被系统杀了服务，重新启用即可。
  const anyAuthEverGranted =
    accessibilityStatus !== 'unauthorized' || notificationStatus !== 'unauthorized'

  return (
    <div className="min-h-screen">
      <PageHeader back title="自动记账" />

      <div className="px-4 py-4 space-y-4 pb-20">
        {/* 总开关 */}
        <div className="glass-card p-4">
          <div className="flex items-center justify-between">
            <div className="flex-1 pr-3">
              <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>
                自动记账
              </div>
              <div className="text-xs text-ink3 mt-1 leading-relaxed">
                支付类通知到了、或在支付页上，就会自动记。没记上的可以一键补。
              </div>
            </div>
            <label className="relative inline-flex items-center cursor-pointer">
              <input
                type="checkbox"
                checked={enabled}
                onChange={e => handleToggle(e.target.checked)}
                disabled={!anyAuthEverGranted}
                className="sr-only peer"
                data-testid="autobook-toggle"
              />
              <div
                className="w-11 h-6 rounded-full peer transition-colors"
                style={{
                  background:
                    !anyAuthEverGranted
                      ? 'var(--glass-border)'
                      : enabled
                        ? 'var(--accent)'
                        : 'var(--glass-border)',
                }}
              >
                <div
                  className="w-5 h-5 rounded-full bg-white shadow transition-transform mt-0.5"
                  style={{ transform: enabled ? 'translateX(22px)' : 'translateX(2px)' }}
                />
              </div>
            </label>
          </div>
          {!anyAuthEverGranted && enabled === false && (
            <div className="text-xs mt-3 px-3 py-2 rounded-lg" style={{ background: 'rgba(245,158,11,0.1)', color: '#b45309' }}>
              请先在下方启用至少一项授权，再打开开关。
            </div>
          )}
        </div>

        {/* 今日 / 待确认 */}
        <div className="glass-card p-4 flex items-center justify-around text-center">
          <div>
            <div className="text-2xl money-num" style={{ color: 'var(--ink)' }} data-testid="autobook-today-count">
              {todayCount}
            </div>
            <div className="text-xs text-ink3 mt-1">今日自动捕获</div>
          </div>
          <div>
            <div className="text-2xl money-num" style={{ color: 'var(--accent)' }} data-testid="autobook-pending-count">
              {pendingCount}
            </div>
            <div className="text-xs text-ink3 mt-1">待确认</div>
          </div>
        </div>

        {/* 两类授权各自的状态与引导 */}
        <div className="glass-card p-4 space-y-3">
          <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>
            授权
          </div>
          <AuthRow
            testId="autobook-auth-accessibility"
            title="无障碍服务（读支付页）"
            description="在支付 App 处于支付 / 账单页时读取屏幕文本，可拿到商户名与订单号。"
            status={accessibilityStatus}
            disabledText="未启用时仍可通过通知通道自动记账，但商户名识别会减弱。"
            reEnableText="曾被停用——可能因系统省电策略或手动关闭。重新启用即可恢复。"
            onOpen={() => handleOpenSystemSettings('accessibility')}
            buttonLabel="前往系统设置"
          />
          <AuthRow
            testId="autobook-auth-notification"
            title="通知使用权（读支付通知）"
            description="监听微信支付、支付宝、云闪付、银行 App 的支付消息通知，不随 App 改版失效。"
            status={notificationStatus}
            disabledText="未启用时仍可通过无障碍通道自动记账，但会漏掉用户不打开支付页的场景。"
            reEnableText="曾被停用——可能因系统省电策略或手动关闭。重新启用即可恢复。"
            onOpen={() => handleOpenSystemSettings('notification_listener')}
            buttonLabel="前往系统设置"
          />
        </div>

        {/* 来源清单（内置三项，只读展示；"从已安装应用添加"挂账后续轮次） */}
        <div className="glass-card p-4">
          <div className="text-sm font-medium mb-2" style={{ color: 'var(--ink)' }}>
            支付来源
          </div>
          <div className="space-y-2">
            {BUILT_IN_SOURCES.map(s => (
              <div
                key={s.pkg}
                className="flex items-center gap-3 px-3 py-2 rounded-lg"
                style={{ background: 'var(--glass)' }}
              >
                <span className="text-lg">{s.icon}</span>
                <span className="flex-1 text-sm" style={{ color: 'var(--ink)' }}>{s.label}</span>
                <span className="text-[10px] px-2 py-0.5 rounded-full" style={{ background: 'var(--glass-border)', color: 'var(--ink3)' }}>
                  内置
                </span>
              </div>
            ))}
          </div>
          <button
            type="button"
            disabled
            className="mt-3 w-full text-xs py-2 rounded-lg text-ink3"
            style={{ background: 'var(--glass)', border: '1px dashed var(--glass-border)' }}
            title="后续轮次实现"
          >
            + 从已安装应用添加（后续版本）
          </button>
        </div>

        {/* 用途说明（显著披露，决策 #18） */}
        <div className="glass-card p-4 space-y-2">
          <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>
            使用说明
          </div>
          <DisclosureRow icon="👁️" text="只读屏幕文本与通知，**不操作任何第三方 App**" />
          <DisclosureRow icon="🔒" text="非支付类内容当场丢弃，不落库、不上行" />
          <DisclosureRow icon="🌐" text="疑似交易文本在联网后上传解析；断网时先存本地，联网补记" />
          <DisclosureRow icon="🚫" text="不申请短信权限、不使用悬浮窗、不读取其他 App 的聊天或验证码" />
          <DisclosureRow icon="✋" text="可随时撤销任一授权或关闭总开关" />
        </div>

        {/* 撤销入口 */}
        <div className="glass-card p-4">
          <div className="text-sm font-medium mb-1" style={{ color: 'var(--ink)' }}>
            撤销
          </div>
          <div className="text-xs text-ink3 leading-relaxed">
            关闭上方开关即可停用自动记账。想彻底收回授权，请在系统设置里分别关闭「无障碍服务」与「通知使用权」。
          </div>
        </div>

        <div className="text-[10px] text-ink3 text-center pt-2 leading-relaxed">
          受系统与应用改版影响，无法保证所有支付都自动记上。<br />
          漏记的可在首页「待确认」一键补。
        </div>
      </div>
    </div>
  )
}

function DisclosureRow({ icon, text }: { icon: string; text: string }) {
  return (
    <div className="flex items-start gap-2 text-xs" style={{ color: 'var(--ink2)' }}>
      <span>{icon}</span>
      {/* 直接渲染，** 用 <strong> 替代 */}
      <div className="flex-1 leading-relaxed">
        {text.split(/(\*\*[^*]+\*\*)/).map((seg, i) =>
          seg.startsWith('**') && seg.endsWith('**')
            ? <strong key={i} style={{ color: 'var(--ink)', fontWeight: 500 }}>{seg.slice(2, -2)}</strong>
            : <span key={i}>{seg}</span>
        )}
      </div>
    </div>
  )
}

function AuthRow({
  title,
  description,
  status,
  disabledText,
  reEnableText,
  onOpen,
  buttonLabel,
  testId,
}: {
  testId: string
  title: string
  description: string
  status: SettingsAuthStatus
  disabledText: string
  reEnableText: string
  onOpen: () => void
  buttonLabel: string
}) {
  const statusBadge = () => {
    if (status === 'enabled') {
      return (
        <span className="text-[10px] px-2 py-0.5 rounded-full" style={{ background: 'rgba(34,197,94,0.15)', color: '#15803d' }}>
          已启用
        </span>
      )
    }
    if (status === 'enabled_but_disabled') {
      return (
        <span className="text-[10px] px-2 py-0.5 rounded-full" style={{ background: 'rgba(245,158,11,0.15)', color: '#b45309' }}>
          已授权但停用
        </span>
      )
    }
    return (
      <span className="text-[10px] px-2 py-0.5 rounded-full" style={{ background: 'var(--glass)', color: 'var(--ink3)' }}>
        未授权
      </span>
    )
  }

  const helperText =
    status === 'enabled'
      ? '服务正在工作。'
      : status === 'enabled_but_disabled'
        ? reEnableText
        : disabledText

  return (
    <div
      className="p-3 rounded-lg space-y-2"
      style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
      data-testid={testId}
    >
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>{title}</div>
        {statusBadge()}
      </div>
      <div className="text-xs text-ink3 leading-relaxed">{description}</div>
      {status !== 'enabled' && (
        <div className="text-xs" style={{ color: status === 'enabled_but_disabled' ? '#b45309' : 'var(--ink3)' }}>
          {helperText}
        </div>
      )}
      {status !== 'enabled' && (
        <button
          type="button"
          onClick={onOpen}
          className="text-xs px-3 py-1.5 rounded-lg"
          style={{ background: 'var(--accent)', color: 'white' }}
          data-testid={`${testId}-button`}
        >
          {buttonLabel}
        </button>
      )}
    </div>
  )
}
