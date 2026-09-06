/**
 * 四时主题测试 — 时段映射、auto/system/manual 模式。
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { getThemeForHour, applyTheme, CHART_COLORS } from '../../design/theme'

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
