/**
 * 四时主题测试 — 时段映射、auto/system/manual 模式、背景透明度（T16-5）。
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { getThemeForHour, applyTheme, CHART_COLORS, applyBgOpacity, useTheme } from '../../design/theme'

describe('getThemeForHour', () => {
  it('returns dawn for 5-9', () => {
    expect(getThemeForHour(5)).toBe('dawn')
    expect(getThemeForHour(9)).toBe('dawn')
  })

  it('returns day for 10-16', () => {
    expect(getThemeForHour(10)).toBe('day')
    expect(getThemeForHour(16)).toBe('day')
  })

  it('returns dusk for 17-19', () => {
    expect(getThemeForHour(17)).toBe('dusk')
    expect(getThemeForHour(19)).toBe('dusk')
  })

  it('returns night for 20-4', () => {
    expect(getThemeForHour(20)).toBe('night')
    expect(getThemeForHour(23)).toBe('night')
    expect(getThemeForHour(0)).toBe('night')
    expect(getThemeForHour(4)).toBe('night')
  })
})

describe('applyTheme', () => {
  beforeEach(() => {
    document.documentElement.className = ''
  })

  it('applies theme class to root', () => {
    applyTheme('dawn')
    expect(document.documentElement.classList.contains('t-dawn')).toBe(true)
  })

  it('removes previous theme class', () => {
    applyTheme('dawn')
    applyTheme('night')
    expect(document.documentElement.classList.contains('t-dawn')).toBe(false)
    expect(document.documentElement.classList.contains('t-night')).toBe(true)
  })
})

describe('T-M4-06a: CHART_COLORS', () => {
  it('defines colors for all four themes', () => {
    expect(CHART_COLORS.dawn).toBeDefined()
    expect(CHART_COLORS.day).toBeDefined()
    expect(CHART_COLORS.dusk).toBeDefined()
    expect(CHART_COLORS.night).toBeDefined()
  })

  it('each theme has at least 6 chart colors', () => {
    for (const theme of ['dawn', 'day', 'dusk', 'night'] as const) {
      expect(CHART_COLORS[theme].length).toBeGreaterThanOrEqual(6)
    }
  })

  it('night theme avoids low-contrast blues on dark backgrounds', () => {
    // Night chart colors should not contain #1D6CB0 (too dark for night bg)
    expect(CHART_COLORS.night).not.toContain('#1D6CB0')
  })
})

describe('T16-5（REQ-61）：背景透明度', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('style')
  })

  it('applyBgOpacity(0) 移除覆盖 —— 回落各主题 CSS 现值', () => {
    applyBgOpacity(40)
    expect(document.documentElement.style.getPropertyValue('--bg-scrim')).not.toBe('')
    applyBgOpacity(0)
    expect(document.documentElement.style.getPropertyValue('--bg-scrim')).toBe('')
  })

  it('applyBgOpacity 按 0.85 上限换算，越界值 clamp', () => {
    applyBgOpacity(100)
    expect(document.documentElement.style.getPropertyValue('--bg-scrim')).toBe('rgba(0, 0, 0, 0.850)')
    applyBgOpacity(50)
    expect(document.documentElement.style.getPropertyValue('--bg-scrim')).toBe('rgba(0, 0, 0, 0.425)')
    applyBgOpacity(150)
    expect(document.documentElement.style.getPropertyValue('--bg-scrim')).toBe('rgba(0, 0, 0, 0.850)')
    applyBgOpacity(-5)
    expect(document.documentElement.style.getPropertyValue('--bg-scrim')).toBe('')
  })

  it('setBgOpacity 写入 aozora-theme 同键扩字段并即时应用', () => {
    const { result } = renderHook(() => useTheme())
    act(() => result.current.setBgOpacity(30))
    expect(result.current.bgOpacity).toBe(30)
    expect(JSON.parse(localStorage.getItem('aozora-theme')!).scrim).toBe(30)
    expect(document.documentElement.style.getPropertyValue('--bg-scrim')).toMatch(/^rgba\(0, 0, 0, 0\.25/)
  })

  it('旧格式（无 scrim）读取为 0，行为与升级前一致', () => {
    localStorage.setItem('aozora-theme', JSON.stringify({ mode: 'manual', manual: 'night' }))
    const { result } = renderHook(() => useTheme())
    expect(result.current.bgOpacity).toBe(0)
    expect(document.documentElement.style.getPropertyValue('--bg-scrim')).toBe('')
  })

  it('已存 scrim 回填并应用', () => {
    localStorage.setItem(
      'aozora-theme',
      JSON.stringify({ mode: 'manual', manual: 'night', scrim: 100 }),
    )
    const { result } = renderHook(() => useTheme())
    expect(result.current.bgOpacity).toBe(100)
    expect(document.documentElement.style.getPropertyValue('--bg-scrim')).toBe('rgba(0, 0, 0, 0.850)')
  })

  it('切换主题模式保留 scrim（不被覆写）', () => {
    localStorage.setItem(
      'aozora-theme',
      JSON.stringify({ mode: 'manual', manual: 'night', scrim: 60 }),
    )
    const { result } = renderHook(() => useTheme())
    act(() => result.current.setManual('dusk'))
    expect(JSON.parse(localStorage.getItem('aozora-theme')!).scrim).toBe(60)
  })
})
