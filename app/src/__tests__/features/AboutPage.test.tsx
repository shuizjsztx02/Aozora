/**
 * AboutPage 组件测试 — 第十轮 T10-4（REQ-20 关于页 / 决策 #9）；
 * T14-7（REQ-44）改版：图标/应用名/标语烘焙进设计稿横幅；
 * T15-6（REQ-55）去 tagline，横幅改为天空裁切图，alt 清空。
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const mockNavigate = vi.hoisted(() => vi.fn())
vi.mock('react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

import { AboutPage } from '../../features/settings/AboutPage'
import { APP_VERSION } from '../../platform/app-version'

const renderPage = () => render(<AboutPage />)

describe('AboutPage', () => {
  it('渲染天空横幅与版本行', () => {
    renderPage()
    const img = screen.getByTestId('about-banner') as HTMLImageElement
    expect(img.tagName).toBe('IMG')
    expect(img.alt).toBe('')
    expect(screen.getByText(`版本 v${APP_VERSION}`)).toBeTruthy()
  })

  it('隐私政策 → /privacy，用户协议 → /terms', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByText('隐私政策'))
    expect(mockNavigate).toHaveBeenLastCalledWith('/privacy')
    await user.click(screen.getByText('用户协议'))
    expect(mockNavigate).toHaveBeenLastCalledWith('/terms')
  })

  it('检查更新 → toast 已是最新版本（不发请求）', async () => {
    const user = userEvent.setup()
    renderPage()
    expect(screen.queryByTestId('about-toast')).toBeNull()
    await user.click(screen.getByText('检查更新'))
    expect(screen.getByTestId('about-toast').textContent).toBe(`当前 v${APP_VERSION}，已是最新版本`)
  })
})
