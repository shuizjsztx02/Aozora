/**
 * App — 根组件。
 *
 * 职责：
 * - 天空画布背景
 * - 路由容器
 * - 全局错误边界
 */

import { ErrorBoundary } from './app/ErrorBoundary'
import { Router } from './app/Router'
import { SkyCanvas } from './design/SkyCanvas'

export default function App() {
  return (
    <ErrorBoundary>
      <SkyCanvas />
      <Router />
    </ErrorBoundary>
  )
}
