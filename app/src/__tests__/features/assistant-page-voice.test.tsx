/**
 * T-M4-04b: AssistantPage 语音集成测试。
 *
 * 验证：
 * - 麦克风按钮渲染 + 状态切换（录音中/非录音）
 * - 实时部分转写显示
 * - 最终转写文本填入输入框
 * - 语音错误显示
 * - 完整链路：录音 → 转写 → 填入输入 → 发送
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { AssistantPage } from '../../features/assistant/AssistantPage'
import type { AssistantPhase } from '../../features/assistant/use-assistant'

// ─── Mocks ───

const mockVoice = vi.hoisted(() => ({
  isRecording: false,
  partialText: '',
  finalText: '',
  error: null as { code: string; message: string } | null,
  startRecording: vi.fn(),
  stopRecording: vi.fn(),
  reset: vi.fn(),
}))

const mockBridge = vi.hoisted(() => ({
  listCategories: vi.fn().mockResolvedValue([]),
  listBooks: vi.fn().mockResolvedValue([{ id: 'book-1', name: '默认账本' }]),
  insertTransaction: vi.fn().mockResolvedValue({ id: 'tx-1' }),
  triggerSync: vi.fn().mockResolvedValue(undefined),
  interpretAI: vi.fn().mockResolvedValue(null),
  validateProposal: vi.fn().mockResolvedValue({ valid: true }),
  executeProposal: vi.fn().mockResolvedValue({ transaction_ids: [], undo_window_seconds: 5 }),
  softDeleteTransaction: vi.fn().mockResolvedValue(undefined),
  isAvailable: vi.fn().mockReturnValue(false),
}))

const mockAssistant = vi.hoisted(() => ({
  messages: [] as unknown[],
  phase: 'idle' as AssistantPhase,
  pendingProposal: null,
  undoRemaining: 0,
  isSlow: false,
  submitInput: vi.fn(),
  confirmEditedProposal: vi.fn(),
  cancelProposal: vi.fn(),
  undoExecution: vi.fn(),
}))

vi.mock('../../platform/hooks/use-voice', () => ({
  useVoice: () => mockVoice,
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

vi.mock('../../features/assistant/use-assistant', () => ({
  useAssistant: () => mockAssistant,
}))

const renderPage = () =>
  render(
    <MemoryRouter>
      <AssistantPage />
    </MemoryRouter>
  )

describe('AssistantPage — 语音集成 (T-M4-04b)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // jsdom 没有 scrollTo，mock 之
    Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
    // 默认非录音状态
    mockVoice.isRecording = false
    mockVoice.partialText = ''
    mockVoice.finalText = ''
    mockVoice.error = null
    mockVoice.startRecording.mockResolvedValue(true)
    mockVoice.stopRecording.mockResolvedValue('')
    mockVoice.reset.mockImplementation(() => {
      mockVoice.partialText = ''
      mockVoice.finalText = ''
    })
    mockAssistant.phase = 'idle'
    mockAssistant.messages = []
  })

  it('渲染麦克风按钮', () => {
    renderPage()
    const mic = screen.getByTestId('voice-mic-button')
    expect(mic).toBeInTheDocument()
    expect(mic).toHaveAttribute('aria-label', '语音输入')
  })

  it('点击麦克风按钮调用 startRecording', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByTestId('voice-mic-button'))
    expect(mockVoice.startRecording).toHaveBeenCalledTimes(1)
  })

  it('录音中时麦克风按钮切换为停止状态', () => {
    mockVoice.isRecording = true
    renderPage()
    const mic = screen.getByTestId('voice-mic-button')
    expect(mic).toHaveAttribute('aria-label', '停止录音')
    // 输入框 placeholder 变为「正在聆听…」
    expect(screen.getByPlaceholderText('正在聆听…')).toBeInTheDocument()
  })

  it('录音中点击麦克风调用 stopRecording', async () => {
    mockVoice.isRecording = true
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByTestId('voice-mic-button'))
    expect(mockVoice.stopRecording).toHaveBeenCalledTimes(1)
  })

  it('录音中显示实时部分转写文本', () => {
    mockVoice.isRecording = true
    mockVoice.partialText = '午饭十五'
    renderPage()
    const partial = screen.getByTestId('voice-partial')
    expect(partial).toBeInTheDocument()
    expect(partial.textContent).toContain('午饭十五')
  })

  it('没有部分转写文本时不显示部分转写条', () => {
    mockVoice.isRecording = true
    mockVoice.partialText = ''
    renderPage()
    expect(screen.queryByTestId('voice-partial')).not.toBeInTheDocument()
  })

  it('语音错误时显示错误提示', () => {
    mockVoice.error = { code: 'permission_denied', message: '需要麦克风权限' }
    renderPage()
    const err = screen.getByTestId('voice-error')
    expect(err).toBeInTheDocument()
    expect(err.textContent).toContain('需要麦克风权限')
  })

  it('转写完成后将文本填入输入框', async () => {
    // 首次渲染：finalText 为空
    const { rerender } = renderPage()
    expect(screen.getByPlaceholderText('输入记账短句…')).toHaveValue('')

    // 模拟转写完成：finalText 更新
    mockVoice.finalText = '午饭 12.5'
    rerender(
      <MemoryRouter>
        <AssistantPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByPlaceholderText('输入记账短句…')).toHaveValue('午饭 12.5')
    })
    expect(mockVoice.reset).toHaveBeenCalled()
  })

  it('完整链路：转写填入后点击发送调用 submitInput', async () => {
    mockVoice.finalText = '晚饭 25'
    const user = userEvent.setup()
    renderPage()

    // 等待转写填入输入框
    await waitFor(() => {
      expect(screen.getByPlaceholderText('输入记账短句…')).toHaveValue('晚饭 25')
    })

    // 点击发送
    await user.click(screen.getByText('发送'))
    expect(mockAssistant.submitInput).toHaveBeenCalledWith('晚饭 25')
  })

  it('处理/执行阶段禁用麦克风按钮', () => {
    mockAssistant.phase = 'processing'
    renderPage()
    expect(screen.getByTestId('voice-mic-button')).toBeDisabled()
  })
})
