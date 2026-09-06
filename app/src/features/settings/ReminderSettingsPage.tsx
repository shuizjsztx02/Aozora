/**
 * ReminderSettingsPage — 提醒设置页。
 *
 * 功能：
 * - 开关每日记账提醒
 * - 设置提醒时间（小时 / 分钟）
 * - 检查/引导通知权限
 *
 * 数据流：前端 → bridge → Kotlin ReminderScheduler → AlarmManager
 */

import { useState, useEffect, useCallback } from 'react'
import { PageHeader } from '../../components/Layout'
import { useBridge } from '../../platform/hooks/use-bridge'

export function ReminderSettingsPage() {
  const bridge = useBridge()
  const [enabled, setEnabled] = useState(false)
  const [hour, setHour] = useState(21)
  const [minute, setMinute] = useState(0)
  const [hasPermission, setHasPermission] = useState(true)
  const [loading, setLoading] = useState(true)

  const loadStatus = useCallback(async () => {
    try {
      const status = await bridge.getReminderStatus()
      setEnabled(status.enabled)
      setHour(status.hour)
      setMinute(status.minute)
      setHasPermission(status.notificationPermission)
    } catch (err) {
      console.error('Failed to load reminder status:', err)
    } finally {
      setLoading(false)
    }
  }, [bridge])

  useEffect(() => {
    loadStatus()
  }, [loadStatus])

  const handleToggle = async (value: boolean) => {
    // R-M4-27: 开启提醒时先检查/请求通知权限
    if (value && !hasPermission) {
      try {
        await bridge.requestPermission('POST_NOTIFICATIONS')
        // 权限结果会通过 onPermissionResult 回调返回，成功时自动设置提醒
        // 为避免复杂状态同步，先提示用户开启权限后再开启提醒
        return
      } catch (err) {
        console.error('Failed to request permission:', err)
      }
    }

    setEnabled(value)
    try {
      await bridge.setReminder(value, hour, minute)
    } catch (err) {
      console.error('Failed to set reminder:', err)
      setEnabled(!value)
    }
  }

  const handleTimeChange = async (newHour: number, newMinute: number) => {
    setHour(newHour)
    setMinute(newMinute)
    try {
      await bridge.setReminder(enabled, newHour, newMinute)
    } catch (err) {
      console.error('Failed to update reminder time:', err)
    }
  }

  const handleCheckPermission = async () => {
    try {
      // R-M4-27 修复：主动请求权限，而非仅检查
      const result = await bridge.requestPermission('POST_NOTIFICATIONS')
      if (!result.pending) {
        // 低版本 Android 或不支持的情况，直接检查
        const checkResult = await bridge.checkReminderPermission()
        setHasPermission(checkResult.granted)
      }
      // 权限结果会通过 onPermissionResult 回调异步返回
    } catch (err) {
      console.error('Failed to request permission:', err)
    }
  }

  // R-M4-27: 监听权限结果回调
  useEffect(() => {
    const unsubscribe = bridge.onPermissionResult((event) => {
      if (event.permission === 'POST_NOTIFICATIONS') {
        if (event.status === 'granted') {
          setHasPermission(true)
        } else if (event.status === 'permanently_denied') {
          // 永久拒绝 → 引导到系统设置
          bridge.openPermissionSettings()
        } else {
          setHasPermission(false)
        }
      }
    })
    return unsubscribe
  }, [bridge])

  if (loading) {
    return (
      <div className="min-h-screen">
        <PageHeader title="提醒设置" />
        <div className="px-4 py-8 text-center text-ink3">加载中...</div>
      </div>
    )
  }

  return (
    <div className="min-h-screen">
      <PageHeader title="提醒设置" />

      <div className="px-4 py-4 space-y-4 pb-20">
        {/* 通知权限提示 */}
        {!hasPermission && (
          <div className="glass-card p-4 border-l-4" style={{ borderColor: 'var(--expense)' }}>
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>
                  通知权限未开启
                </div>
                <div className="text-xs text-ink3 mt-1">
                  开启通知权限后才能收到提醒
                </div>
              </div>
              <button
                onClick={handleCheckPermission}
                className="text-xs px-3 py-1.5 rounded-lg"
                style={{ background: 'var(--accent)', color: 'white' }}
              >
                去开启
              </button>
            </div>
          </div>
        )}

        {/* 提醒开关 */}
        <div className="glass-card p-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>
                每日记账提醒
              </div>
              <div className="text-xs text-ink3 mt-1">
                每天定时提醒你记录当日花销
              </div>
            </div>
            <label className="relative inline-flex items-center cursor-pointer">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => handleToggle(e.target.checked)}
                className="sr-only peer"
              />
              <div className="w-11 h-6 rounded-full peer transition-colors"
                style={{ background: enabled ? 'var(--accent)' : 'var(--glass-border)' }}>
                <div className="w-5 h-5 rounded-full bg-white shadow transition-transform mt-0.5"
                  style={{ transform: enabled ? 'translateX(22px)' : 'translateX(2px)' }} />
              </div>
            </label>
          </div>
        </div>

        {/* 时间选择 */}
        {enabled && (
          <div className="glass-card p-4">
            <div className="text-sm font-medium mb-3" style={{ color: 'var(--ink)' }}>
              提醒时间
            </div>
            <div className="flex items-center gap-2 justify-center">
              {/* 小时选择 */}
              <div className="flex flex-col items-center">
                <button
                  onClick={() => handleTimeChange((hour + 1) % 24, minute)}
                  className="w-16 h-10 rounded-lg flex items-center justify-center text-lg"
                  style={{ background: 'var(--glass)', color: 'var(--ink)' }}
                >
                  ▲
                </button>
                <div className="text-3xl font-light py-2 money-num" style={{ color: 'var(--ink)' }}>
                  {String(hour).padStart(2, '0')}
                </div>
                <button
                  onClick={() => handleTimeChange((hour - 1 + 24) % 24, minute)}
                  className="w-16 h-10 rounded-lg flex items-center justify-center text-lg"
                  style={{ background: 'var(--glass)', color: 'var(--ink)' }}
                >
                  ▼
                </button>
                <div className="text-xs text-ink3 mt-1">时</div>
              </div>

              <div className="text-3xl font-light" style={{ color: 'var(--ink)' }}>:</div>

              {/* 分钟选择 */}
              <div className="flex flex-col items-center">
                <button
                  onClick={() => handleTimeChange(hour, (minute + 1) % 60)}
                  className="w-16 h-10 rounded-lg flex items-center justify-center text-lg"
                  style={{ background: 'var(--glass)', color: 'var(--ink)' }}
                >
                  ▲
                </button>
                <div className="text-3xl font-light py-2 money-num" style={{ color: 'var(--ink)' }}>
                  {String(minute).padStart(2, '0')}
                </div>
                <button
                  onClick={() => handleTimeChange(hour, (minute - 1 + 60) % 60)}
                  className="w-16 h-10 rounded-lg flex items-center justify-center text-lg"
                  style={{ background: 'var(--glass)', color: 'var(--ink)' }}
                >
                  ▼
                </button>
                <div className="text-xs text-ink3 mt-1">分</div>
              </div>
            </div>
          </div>
        )}

        {/* 说明 */}
        <div className="text-xs text-ink3 text-center py-4">
          提醒时间仅在应用未运行时生效<br />
          应用内可通过首页快捷记账按钮直接记录
        </div>
      </div>
    </div>
  )
}
