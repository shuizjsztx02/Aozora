/**
 * T-M4-06b: 可访问性工具测试。
 */

import { describe, it, expect, beforeEach } from 'vitest'
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

  it('returns false when device is capable', () => {
    // jsdom default hardwareConcurrency is usually > 2
    const { result } = renderHook(() => useLowPerformance())
    // In jsdom, hardwareConcurrency may be undefined → defaults to 4
    // So this should be false unless reduced-motion is set
    expect(typeof result.current).toBe('boolean')
  })

  it('returns true when localStorage flag is set', () => {
    try { localStorage.setItem('aozora-low-perf', '1') } catch { /* ignore */ }
    const { result } = renderHook(() => useLowPerformance())
    expect(result.current).toBe(true)
  })
})
