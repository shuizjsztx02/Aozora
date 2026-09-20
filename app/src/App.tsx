/**
 * App — 根组件。
 *
 * 职责：
 * - 低性能降级标记（T17-2 起：装饰层下线，仅保留 .low-perf 副作用）
 * - 路由容器
 * - 全局错误边界
 * - 同步监听初始化（全应用生命周期）
 * - T-M4-06b: 字体放大适配
 */

import { useEffect } from 'react'
import { ErrorBoundary } from './app/ErrorBoundary'
import { Router } from './app/Router'
import { useLowPerformance } from './design/a11y'
import { useInitSync } from './platform/hooks/use-bridge'

/**
 * T-M4-06b: 检测系统字体缩放并添加 text-scale-13 标记 class。
 * R-M4-12: 仅作布局适配信号，不覆盖 font-size（CSS 中无 font-size 规则）。
 */
function FontScaleAdapter() {
  useEffect(() => {
    const root = document.documentElement
    const check = () => {
      const computed = parseFloat(getComputedStyle(root).fontSize)
      if (computed >= 20.8) { // 16 * 1.3
        root.classList.add('text-scale-13')
      } else {
        root.classList.remove('text-scale-13')
      }
    }
    check()
    window.addEventListener('resize', check)
    return () => {
      window.removeEventListener('resize', check)
      root.classList.remove('text-scale-13')
    }
  }, [])
  return null
}

export default function App() {
  useInitSync()
  // T17-2（REQ-64）：装饰层下线后，此处的调用只保留其副作用 —— 往 root 打
  // .low-perf 标记（index.css 据此关掉 glass 的 backdrop-filter 降级）。
  useLowPerformance()
  return (
    <ErrorBoundary>
      <FontScaleAdapter />
      <Router />
    </ErrorBoundary>
  )
}
