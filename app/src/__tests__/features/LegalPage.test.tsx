/**
 * LegalPage 返回栏测试 — 第十二轮 T12-4（REQ-34）。
 *
 * 覆盖（卡验收）：
 * - 无 router state（从关于页进入的形态）仍渲染 PageHeader 返回钮
 * - 点击返回 → navigate(-1)
 * - 标题 / 版本行渲染（h1 正文化后仍在文档流）
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const mockNavigate = vi.hoisted(() => vi.fn())
vi.mock('react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

import { PrivacyPage, TermsPage } from '../../features/legal/LegalPage'

describe('LegalPage 返回栏（T12-4）', () => {
  it('/privacy 无 state 也有返回钮，点击 navigate(-1)', async () => {
    const user = userEvent.setup()
    render(<PrivacyPage />)
    const back = screen.getByRole('button', { name: '返回' })
    expect(back).toBeTruthy()
    expect(screen.getByText('隐私政策')).toBeTruthy()
    await user.click(back)
    expect(mockNavigate).toHaveBeenCalledWith(-1)
  })

  it('/terms 同样渲染统一返回钮', async () => {
    const user = userEvent.setup()
    render(<TermsPage />)
    expect(screen.getByRole('button', { name: '返回' })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '返回' }))
    expect(mockNavigate).toHaveBeenCalledWith(-1)
  })
})
