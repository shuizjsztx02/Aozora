import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import App from '../App'

describe('Test framework readiness', () => {
  it('vitest and jest-dom matchers are available', () => {
    expect(document.body).toBeInTheDocument()
  })
})

describe('App', () => {
  it('renders the routed shell (默认路由 → 首启隐私步骤)，且不再挂装饰层', () => {
    render(<App />)
    // 默认路由（未完成首启引导）→ OnboardingPage 隐私步骤
    expect(screen.getByTestId('consent-btn')).toBeInTheDocument()
    // T17-2（REQ-64）：旧 UI 装饰层已整体下线
    expect(document.querySelector('.sky-fixed')).toBeNull()
    expect(screen.queryByTestId('sky-canvas')).not.toBeInTheDocument()
  })
})
