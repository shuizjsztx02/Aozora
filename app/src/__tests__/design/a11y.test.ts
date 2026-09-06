/**
 * T-M4-06b: 可访问性工具测试。
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
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
    // Explicitly set high values to ensure test stability across environments
    const nav = navigator as any
    const origCores = nav.hardwareConcurrency
    const origMem = nav.deviceMemory
    Object.defineProperty(nav, 'hardwareConcurrency', { value: 8, configurable: true })
    Object.defineProperty(nav, 'deviceMemory', { value: 8, configurable: true })

    // Mock matchMedia to return no reduced motion
    const origMatchMedia = window.matchMedia
    window.matchMedia = vi.fn().mockImplementation(query => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))

    const { result } = renderHook(() => useLowPerformance())
    expect(result.current).toBe(false)
    // 验证 classList 操作：低性能标记未添加
    expect(document.documentElement.classList.contains('low-perf')).toBe(false)

    // Restore
    Object.defineProperty(nav, 'hardwareConcurrency', { value: origCores, configurable: true })
    Object.defineProperty(nav, 'deviceMemory', { value: origMem, configurable: true })
    window.matchMedia = origMatchMedia
  })

  it('returns true when hardwareConcurrency <= 2', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const nav = navigator as any
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
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const nav = navigator as any
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
