import { Component, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
  retryCount?: number
}

/** 全局错误边界 — 捕获渲染异常，显示友好错误页 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null, retryCount: 0 }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: { componentStack: string }) {
    console.error('[ErrorBoundary] Uncaught rendering error:', error, info.componentStack)
  }

  render() {
    if (this.state.hasError) {
      const exhausted = (this.state.retryCount ?? 0) >= 3
      return (
        <div className="min-h-screen flex flex-col items-center justify-center p-6" style={{ color: 'var(--ink)' }}>
          <div className="glass-card p-8 text-center max-w-sm">
            <div className="text-4xl mb-4">{exhausted ? '💔' : '😵'}</div>
            <h1 className="text-lg font-semibold mb-2">
              {exhausted ? '需要帮助' : '出了点问题'}
            </h1>
            <p className="text-sm text-ink2 mb-4">
              {exhausted
                ? '问题持续出现，请尝试重启应用'
                : '页面渲染时遇到了问题，请稍后重试'}
            </p>
            {!exhausted && (
              <button
                onClick={() => this.setState(prev => ({ hasError: false, error: null, retryCount: (prev.retryCount ?? 0) + 1 }))}
                className="px-4 py-2 rounded-lg text-sm font-medium"
                style={{ background: 'var(--accent)', color: 'white' }}
              >
                重试
              </button>
            )}
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
