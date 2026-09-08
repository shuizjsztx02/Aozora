/**
 * FeedbackPage 组件测试 — T-M5-05c。
 *
 * 覆盖：
 * - 渲染：页面标题、三个类别按钮、文本域、联系方式输入
 * - 验证：空内容时提交禁用、有内容时启用、字符计数
 * - 提交：正确参数传递、成功状态、提交中禁用、失败后恢复
 * - 类别选择：不同类别传入正确参数
 */
import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { NativeBridge } from '../../platform/bridge/native-bridge'
import { FeedbackPage } from '../../features/settings/FeedbackPage'

// ─── Runtime spy (no hoisting issues) ───

const mockSubmitFeedback = vi.fn()

beforeAll(() => {
  vi.spyOn(NativeBridge.prototype, 'submitFeedback').mockImplementation(mockSubmitFeedback)
})

const renderPage = () => render(
  <MemoryRouter>
    <FeedbackPage />
  </MemoryRouter>,
)

// ─── Tests ───

describe('FeedbackPage', () => {
  beforeEach(() => {
    mockSubmitFeedback.mockReset()
    mockSubmitFeedback.mockResolvedValue({ id: 'fb-123' })
  })

  // ─── Render ───

  describe('Render', () => {
    it('renders back button without white title header', () => {
      renderPage()
      // 按需求移除标题文字栏，仅保留透明返回按钮
      expect(screen.queryByText('反馈')).not.toBeInTheDocument()
      expect(screen.getByLabelText('返回')).toBeInTheDocument()
    })

    it('renders three category buttons', () => {
      renderPage()
      expect(screen.getByRole('button', { name: /Bug/ })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /建议/ })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /其他/ })).toBeInTheDocument()
    })

    it('renders text area with placeholder', () => {
      renderPage()
      expect(screen.getByPlaceholderText(/请描述你遇到的问题/)).toBeInTheDocument()
    })

    it('renders optional contact input', () => {
      renderPage()
      expect(screen.getByPlaceholderText(/邮箱/)).toBeInTheDocument()
    })
  })

  // ─── Validation ───

  describe('Validation', () => {
    it('submit button disabled when content is empty', () => {
      renderPage()
      const submitBtn = screen.getByRole('button', { name: /提交反馈/ })
      expect(submitBtn).toBeDisabled()
    })

    it('submit button enabled when content has text', () => {
      renderPage()
      const textarea = screen.getByPlaceholderText(/请描述/)
      fireEvent.change(textarea, { target: { value: '测试内容' } })
      const submitBtn = screen.getByRole('button', { name: /提交反馈/ })
      expect(submitBtn).not.toBeDisabled()
    })

    it('shows character count within limit', () => {
      renderPage()
      const textarea = screen.getByPlaceholderText(/请描述/)
      fireEvent.change(textarea, { target: { value: 'hello' } })
      expect(screen.getByText('5/4000')).toBeInTheDocument()
    })
  })

  // ─── Submission ───

  describe('Submission', () => {
    it('calls bridge.submitFeedback with correct params on submit', async () => {
      renderPage()
      const textarea = screen.getByPlaceholderText(/请描述/)
      fireEvent.change(textarea, { target: { value: 'App crashes on startup' } })

      // Select suggestion via direct query
      fireEvent.click(screen.getByRole('button', { name: /建议/ }))

      const contactInput = screen.getByPlaceholderText(/邮箱/)
      fireEvent.change(contactInput, { target: { value: 'test@example.com' } })

      fireEvent.click(screen.getByRole('button', { name: /提交反馈/ }))

      await waitFor(() => {
        expect(mockSubmitFeedback).toHaveBeenCalledWith({
          category: 'suggestion',
          content: 'App crashes on startup',
          contact: 'test@example.com',
        })
      })
    })

    it('shows success state after successful submission', async () => {
      mockSubmitFeedback.mockResolvedValue({ id: 'fb-success' })
      renderPage()
      const textarea = screen.getByPlaceholderText(/请描述/)
      fireEvent.change(textarea, { target: { value: 'Great app!' } })
      fireEvent.click(screen.getByRole('button', { name: /提交反馈/ }))

      await waitFor(() => {
        expect(screen.getByText('感谢你的反馈！')).toBeInTheDocument()
      })
    })

    it('disables submit button during submission', async () => {
      mockSubmitFeedback.mockReturnValue(new Promise(() => {}))
      renderPage()
      const textarea = screen.getByPlaceholderText(/请描述/)
      fireEvent.change(textarea, { target: { value: 'slow submission' } })
      fireEvent.click(screen.getByRole('button', { name: /提交反馈/ }))

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /提交中/ })).toBeDisabled()
      })
    })

    it('shows error when submission fails', async () => {
      mockSubmitFeedback.mockRejectedValueOnce(new Error('connection refused'))

      renderPage()
      const textarea = screen.getByPlaceholderText(/请描述/)
      fireEvent.change(textarea, { target: { value: 'bug report' } })
      fireEvent.click(screen.getByRole('button', { name: /提交反馈/ }))

      await waitFor(() => {
        expect(screen.getByText(/提交失败/)).toBeInTheDocument()
      })
    })

    it('enables submit button again after failed submission', async () => {
      mockSubmitFeedback.mockRejectedValueOnce(new Error('server down'))

      renderPage()
      const textarea = screen.getByPlaceholderText(/请描述/)
      fireEvent.change(textarea, { target: { value: 'report' } })
      fireEvent.click(screen.getByRole('button', { name: /提交反馈/ }))

      await waitFor(() => {
        const btn = screen.getByRole('button', { name: /提交反馈/ })
        expect(btn).not.toBeDisabled()
      })
    })
  })

  // ─── Category selection ───

  describe('Category selection', () => {
    it('sends suggestion category when selected', async () => {
      renderPage()
      const textarea = screen.getByPlaceholderText(/请描述/)
      fireEvent.change(textarea, { target: { value: 'test' } })

      fireEvent.click(screen.getByRole('button', { name: /建议/ }))
      fireEvent.click(screen.getByRole('button', { name: /提交反馈/ }))

      await waitFor(() => {
        expect(mockSubmitFeedback).toHaveBeenCalledWith(
          expect.objectContaining({ category: 'suggestion' }),
        )
      })
    })

    it('sends other category when selected', async () => {
      renderPage()
      const textarea = screen.getByPlaceholderText(/请描述/)
      fireEvent.change(textarea, { target: { value: 'test' } })

      fireEvent.click(screen.getByRole('button', { name: /其他/ }))
      fireEvent.click(screen.getByRole('button', { name: /提交反馈/ }))

      await waitFor(() => {
        expect(mockSubmitFeedback).toHaveBeenCalledWith(
          expect.objectContaining({ category: 'other' }),
        )
      })
    })
  })
})
