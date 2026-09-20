/**
 * 四时主题管理 — 时段映射、主题切换、auto/manual 模式、背景透明度。
 *
 * 依赖：React hooks + theme-backgrounds（自定义背景应用，type-only 反向依赖）
 */

import { useState, useEffect, useCallback } from 'react'
import { applyThemeBackground } from './theme-backgrounds'

export type ThemeName = 'dawn' | 'day' | 'dusk' | 'night'
export type ThemeMode = 'auto' | 'system' | 'manual'

/** T-M4-06a: 各主题图表色板 — 确保在各主题背景上可辨识 */
export const CHART_COLORS: Record<ThemeName, string[]> = {
  dawn: ['#D95F3B', '#1D6CB0', '#1E9E6A', '#D96F5A', '#6E7C90', '#A8730F', '#4E4066', '#8B5E3C'],
  day:  ['#D95F3B', '#1D6CB0', '#1E9E6A', '#E9C56A', '#D96F5A', '#6E7C90', '#4E4066', '#A8730F'],
  dusk: ['#E8756A', '#E9C56A', '#4CC38A', '#D96F5A', '#B6BCCF', '#A8730F', '#7A83A3', '#1D6CB0'],
  night: ['#E8756A', '#E9C56A', '#4CC38A', '#8690B0', '#D96F5A', '#B6BCCF', '#A8730F', '#5A7AB0'],
}

/** 根据当前小时推导时段主题 */
export function getThemeForHour(hour: number): ThemeName {
  if (hour >= 5 && hour < 10) return 'dawn'
  if (hour >= 10 && hour < 17) return 'day'
  if (hour >= 17 && hour < 20) return 'dusk'
  return 'night'
}

/** 获取当前时刻对应的主题 */
export function getCurrentAutoTheme(): ThemeName {
  return getThemeForHour(new Date().getHours())
}

/** 检测系统是否偏好深色模式 */
export function getSystemPrefersDark(): boolean {
  if (typeof window === 'undefined') return false
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
}

/** 系统深色时的主题映射 */
function systemTheme(): ThemeName {
  return getSystemPrefersDark() ? 'night' : 'day'
}

const THEME_CLASS = 't-dawn t-day t-dusk t-night' as const

/** 将主题应用到 document root */
export function applyTheme(theme: ThemeName): void {
  const root = document.documentElement
  root.classList.remove(...THEME_CLASS.split(' '))
  root.classList.add(`t-${theme}`)
}

/**
 * T17-1（REQ-63）：背景图淡出度 0–100 → 覆盖 root 的 --bg-veil
 * （body::before 消费，纱色 = 该主题底色渐变）。0 = 无纱，背景图原样。
 */
export function applyBgOpacity(value: number): void {
  const v = Math.max(0, Math.min(100, Math.round(value)))
  const root = document.documentElement
  if (v === 0) {
    root.style.removeProperty('--bg-veil')
  } else {
    root.style.setProperty('--bg-veil', (v / 100).toFixed(3))
  }
}

interface ThemeState {
  mode: ThemeMode
  manual: ThemeName
  /**
   * T17-1（REQ-63）：背景图淡出度 0–100；0 = 背景图原样（纱 opacity 0）
   * 存储字段名仍为 scrim（不迁移、不新增键）。
   */
  scrim: number
}

/** 解析已存储的主题模式（旧格式无 scrim → 0，行为与升级前一致） */
function loadThemeState(): ThemeState {
  try {
    const stored = localStorage.getItem('aozora-theme')
    if (stored) {
      const parsed = JSON.parse(stored)
      if (parsed.mode && parsed.manual) {
        const scrim =
          typeof parsed.scrim === 'number' && parsed.scrim >= 0 && parsed.scrim <= 100
            ? parsed.scrim
            : 0
        return { mode: parsed.mode, manual: parsed.manual, scrim }
      }
    }
  } catch { /* ignore */ }
  return { mode: 'auto', manual: 'day', scrim: 0 }
}

function saveThemeState(state: ThemeState): void {
  try {
    localStorage.setItem('aozora-theme', JSON.stringify(state))
  } catch { /* ignore */ }
}

/** 计算当前实际主题 */
function resolveTheme(mode: ThemeMode, manual: ThemeName): ThemeName {
  switch (mode) {
    case 'auto': return getCurrentAutoTheme()
    case 'system': return systemTheme()
    case 'manual': return manual
  }
}

/**
 * useTheme — 主题管理 hook。
 * 返回当前主题 + 切换函数。
 */
export function useTheme() {
  const [state, setState] = useState(loadThemeState)
  const [theme, setTheme] = useState(() => resolveTheme(state.mode, state.manual))

  const updateTheme = useCallback((mode: ThemeMode, manual?: ThemeName) => {
    setState(prev => {
      const next = { mode, manual: manual ?? prev.manual, scrim: prev.scrim }
      saveThemeState(next)
      const resolved = resolveTheme(next.mode, next.manual)
      setTheme(resolved)
      applyTheme(resolved)
      return next
    })
  }, [])

  /** T16-5（REQ-61）：设置背景透明度（0–100），即时写 localStorage + --bg-veil */
  const setBgOpacity = useCallback((value: number) => {
    const v = Math.max(0, Math.min(100, Math.round(value)))
    setState(prev => {
      const next = { ...prev, scrim: v }
      saveThemeState(next)
      applyBgOpacity(v)
      return next
    })
  }, [])

  // 应用初始主题 + 背景透明度
  useEffect(() => {
    applyTheme(theme)
    applyBgOpacity(state.scrim)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // T16-5（REQ-61）：自定义背景随主题变化重新应用（无自定义则回落类内默认图）
  useEffect(() => {
    void applyThemeBackground(theme)
  }, [theme])

  // auto 模式：每分钟检查时段变化
  useEffect(() => {
    if (state.mode !== 'auto') return
    const interval = setInterval(() => {
      const newTheme = getCurrentAutoTheme()
      setTheme(prev => {
        if (prev !== newTheme) {
          applyTheme(newTheme)
          return newTheme
        }
        return prev
      })
    }, 60_000)
    return () => clearInterval(interval)
  }, [state.mode])

  // system 模式：监听系统深色偏好变化
  useEffect(() => {
    if (state.mode !== 'system') return
    const mq = window.matchMedia?.('(prefers-color-scheme: dark)')
    if (!mq) return
    const handler = () => {
      const newTheme = systemTheme()
      setTheme(prev => {
        if (prev !== newTheme) {
          applyTheme(newTheme)
          return newTheme
        }
        return prev
      })
    }
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [state.mode])

  return {
    theme,
    mode: state.mode,
    manualTheme: state.manual,
    /** T17-1（REQ-63）：背景图淡出度 0–100（0 = 背景图原样） */
    bgOpacity: state.scrim,
    setBgOpacity,
    setAuto: () => updateTheme('auto'),
    setSystem: () => updateTheme('system'),
    setManual: (t: ThemeName) => updateTheme('manual', t),
  }
}

/** T-M4-06a: 获取当前主题对应的图表色板 */
export function useChartColors(): string[] {
  const { theme } = useTheme()
  return CHART_COLORS[theme]
}
