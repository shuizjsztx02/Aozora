/**
 * T-M4-06b: 可访问性工具测试。
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useReducedMotion, useLowPerformance } from '../../design/a11y'

describe('useReducedMotion', () => {
  it('returns false by default', () => {
    const { result } = renderHook(() => useReducedMotion())
    expect(result.current).toBe(false)
  })
})

describe('useLowPerformance', () => {
  beforeEach(() => {
    try { localStorage.clear() } catch { /* ignore */ }
    document.documentElement.classList.remove('low-perf')
  })

  it('returns false when device has sufficient resources', () => {
    // jsdom defaults: hardwareConcurrency undefined → 4, deviceMemory undefined → 4
    const { result } = renderHook(() => useLowPerformance())
    expect(result.current).toBe(false)
    // 验证 classList 操作：低性能标记未添加
    expect(document.documentElement.classList.contains('low-perf')).toBe(false)
  })

  it('returns true when hardwareConcurrency <= 2', () => {
    const nav = navigator as Record<string, unknown>
    const originalConcurrency = nav.hardwareConcurrency
    Object.defineProperty(nav, 'hardwareConcurrency', { value: 2, configurable: true })
    try {
      const { result } = renderHook(() => useLowPerformance())
      expect(result.current).toBe(true)
      expect(document.documentElement.classList.contains('low-perf')).toBe(true)
    } finally {
      Object.defineProperty(nav, 'hardwareConcurrency', { value: originalConcurrency, configurable: true })
    }
  })

  it('returns true when deviceMemory <= 2', () => {
    const nav = navigator as Record<string, unknown>
    const originalMemory = nav.deviceMemory
    Object.defineProperty(nav, 'deviceMemory', { value: 2, configurable: true })
    try {
      const { result } = renderHook(() => useLowPerformance())
      expect(result.current).toBe(true)
      expect(document.documentElement.classList.contains('low-perf')).toBe(true)
    } finally {
      Object.defineProperty(nav, 'deviceMemory', { value: originalMemory, configurable: true })
    }
  })

  it('returns true when localStorage flag is set', () => {
    try { localStorage.setItem('aozora-low-perf', '1') } catch { /* ignore */ }
    const { result } = renderHook(() => useLowPerformance())
    expect(result.current).toBe(true)
    expect(document.documentElement.classList.contains('low-perf')).toBe(true)
  })

  it('cleanup removes low-perf class on unmount', () => {
    try { localStorage.setItem('aozora-low-perf', '1') } catch { /* ignore */ }
    const { unmount } = renderHook(() => useLowPerformance())
    expect(document.documentElement.classList.contains('low-perf')).toBe(true)
    unmount()
    expect(document.documentElement.classList.contains('low-perf')).toBe(false)
  })
})
