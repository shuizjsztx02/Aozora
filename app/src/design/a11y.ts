/**
 * 可访问性与性能适配工具。
 *
 * T-M4-06b: reduced motion、low-performance 检测与自动降级。
 */

import { useState, useEffect } from 'react'

/** 检测用户是否偏好减少动画 */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => {
    if (typeof window === 'undefined') return false
    return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  })

  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    if (!mq) return
    const handler = (e: MediaQueryListEvent) => setReduced(e.matches)
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])

  return reduced
}

/**
 * 低性能设备检测。
 *
 * 启发式规则：
 * - navigator.hardwareConcurrency ≤ 2
 * - navigator.deviceMemory ≤ 2（Chrome only）
 * - 匹配 prefers-reduced-motion 也视为低性能信号
 * - localStorage 手动标记 aozora-low-perf=1
 */
export function useLowPerformance(): boolean {
  const [lowPerf] = useState(() => {
    if (typeof window === 'undefined') return false
    const nav = navigator as any
    const cores = nav.hardwareConcurrency ?? 4
    const mem = nav.deviceMemory ?? 4
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    // 也读 localStorage 手动标记
    let manual = false
    try { manual = localStorage.getItem('aozora-low-perf') === '1' } catch { /* ignore */ }
    return cores <= 2 || mem <= 2 || reducedMotion || manual
  })

  // 同步 class 到低性能模式
  useEffect(() => {
    const flag = lowPerf ? '1' : '0'
    try { localStorage.setItem('aozora-low-perf', flag) } catch { /* ignore */ }
    document.documentElement.classList.toggle('low-perf', lowPerf)
    return () => document.documentElement.classList.remove('low-perf')
  }, [lowPerf])

  return lowPerf
}

/**
 * 检测用户是否放大了系统字体（>1.3×）。
 * 当 scale ≥ 1.3 时，在 html 上添加 text-scale-13 class 作为布局适配信号。
 * R-M4-12: 不覆盖 font-size，让浏览器自然使用用户的系统字体缩放设置。
 *
 * NOTE: 当前未直接使用——App.tsx 的 FontScaleAdapter 内联实现了相同逻辑
 *       并附带 resize 监听（解决 R-M4-14）。保留此导出供未来复用。
 */
export function useFontScale(): number {
  const [scale, setScale] = useState(1)

  useEffect(() => {
    // 通过比较 rem 与 root font-size 来检测
    const root = document.documentElement
    const computed = parseFloat(getComputedStyle(root).fontSize)
    const base = 16
    const detectedScale = computed / base
    setScale(detectedScale)

    // R-M4-12: 仅添加标记 class，不覆盖 font-size（避免降级用户实际设置）
    // CSS 可利用此类做 padding/gap 等布局适配
    if (detectedScale >= 1.3) {
      root.classList.add('text-scale-13')
    } else {
      root.classList.remove('text-scale-13')
    }
    return () => root.classList.remove('text-scale-13')
  }, [])

  return scale
}
