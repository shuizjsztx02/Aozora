/**
 * 四时主题管理 — 时段映射、主题切换、auto/manual 模式。
 *
 * 依赖：无（纯函数 + React hooks）
 */

import { useState, useEffect, useCallback } from 'react'

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

/** 解析已存储的主题模式 */
function loadThemeState(): { mode: ThemeMode; manual: ThemeName } {
  try {
    const stored = localStorage.getItem('aozora-theme')
    if (stored) {
      const parsed = JSON.parse(stored)
      if (parsed.mode && parsed.manual) return parsed
    }
  } catch { /* ignore */ }
  return { mode: 'auto', manual: 'day' }
}

function saveThemeState(mode: ThemeMode, manual: ThemeName): void {
  try {
    localStorage.setItem('aozora-theme', JSON.stringify({ mode, manual }))
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
      const next = { mode, manual: manual ?? prev.manual }
      saveThemeState(next.mode, next.manual)
      const resolved = resolveTheme(next.mode, next.manual)
      setTheme(resolved)
      applyTheme(resolved)
      return next
    })
  }, [])

  // 应用初始主题
  useEffect(() => {
    applyTheme(theme)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
