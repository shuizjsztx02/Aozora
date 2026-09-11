/**
 * T9-8（REQ-16）: 聊天页图片上传链路测试。
 *
 * 验证：
 * - 🖼️ 按钮渲染（T12-1 REQ-29 ② 输入行重排后：[🎤][🖼️][输入][发送] DOM 顺序）
 * - 完整链路：pickImage → parseImage → 批量卡渲染 → batch-confirm →
 *   executeProposal（token=chat_img_*）→ 卡片收起 + 落「已记录 N 笔交易」+ triggerSync
 * - parse 失败 → 红条 + 重试（重试续 parse 不重新选图）
 * - pick 取消 → 静默回 idle 无红条
 * - 账本未就绪 → 红条「账本未就绪」
 * - 批量卡显示期间文本输入不禁用（决策 #11，与单卡链路互不干扰）
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { AssistantPage } from '../../features/assistant/AssistantPage'

const mockVoice = vi.hoisted(() => ({
  isRecording: false,
  partialText: '',
  finalText: '',
  error: null,
  startRecording: vi.fn(),
  stopRecording: vi.fn(),
  reset: vi.fn(),
}))

const mockBridge = vi.hoisted(() => ({
  listCategories: vi.fn().mockResolvedValue([]),
  listBooks: vi.fn().mockResolvedValue([{ id: 'book-1', name: '默认账本', isMaster: false }]),
  listAccounts: vi.fn().mockResolvedValue([]),
  getMeta: vi.fn().mockResolvedValue({}),
  listPersonas: vi.fn().mockResolvedValue([]),
  // T13-5（REQ-48）：现有对象名单装载（context 注入 + config 解析数据源）
  listSavingsGoals: vi.fn().mockResolvedValue([]),
  listBudgets: vi.fn().mockResolvedValue([]),
  pickImage: vi.fn().mockResolvedValue({ attachmentId: 'att-1', mimeType: 'image/jpeg', fileSize: 12345 }),
  parseImage: vi.fn(),
  executeProposal: vi.fn().mockResolvedValue({ transaction_ids: ['t1', 't2'], undo_window_seconds: 0 }),
  triggerSync: vi.fn().mockResolvedValue(undefined),
  isAvailable: vi.fn().mockReturnValue(false),
}))

const mockAssistant = vi.hoisted(() => ({
  messages: [] as unknown[],
  phase: 'idle',
  activeProposal: null as unknown,
  cardStatus: null as unknown,
  isSlow: false,
  submitInput: vi.fn(),
  editProposal: vi.fn(),
  undoExecution: vi.fn(),
  appendAssistantMessage: vi.fn(),
}))

vi.mock('../../platform/hooks/use-voice', () => ({ useVoice: () => mockVoice }))
vi.mock('../../platform/hooks/use-bridge', () => ({ useBridge: () => mockBridge }))
vi.mock('../../features/assistant/use-assistant', () => ({ useAssistant: () => mockAssistant }))
// T12-1：页面新增 usePermissions（mic 按需授权）——本文件不点 mic，默认已授予即可
vi.mock('../../platform/hooks/use-permissions', () => ({
  usePermissions: () => ({
    canRecord: true,
    loading: false,
    status: {
      permissions: { RECORD_AUDIO: 'granted', CAMERA: 'not_requested', READ_MEDIA_IMAGES: 'not_requested', POST_NOTIFICATIONS: 'not_requested' },
      canRecordAudio: true,
      canUseCamera: false,
      canAccessImages: false,
    },
    canCamera: false,
    canGallery: false,
    shouldGuideSettings: false,
    requestPermission: vi.fn().mockResolvedValue(undefined),
    openSettings: vi.fn().mockResolvedValue(undefined),
    refresh: vi.fn().mockResolvedValue(undefined),
  }),
}))

const batchProposal = {
  proposalId: 'prop-batch',
  action: 'add_tx_batch',
  confidence: 0.9,
  requiresConfirmation: true,
  message: '',
  warnings: [],
  transactions: [
    { type: 'expense', amountCents: 1200, categoryId: 'c1', bizDate: '2026-09-09', note: '米饭' },
    { type: 'expense', amountCents: 300, categoryId: 'c2', bizDate: '2026-09-09', note: '可乐' },
  ],
}

const bridgeError = (message: string, code?: string) =>
  code ? Object.assign(new Error(message), { code }) : new Error(message)

const renderPage = () =>
  render(
    <MemoryRouter>
      <AssistantPage />
    </MemoryRouter>,
  )

describe('AssistantPage — 图片上传链路 (T9-8)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
    mockAssistant.phase = 'idle'
    mockAssistant.messages = []
    mockBridge.listBooks.mockResolvedValue([{ id: 'book-1', name: '默认账本', isMaster: false }])
    mockBridge.pickImage.mockResolvedValue({ attachmentId: 'att-1', mimeType: 'image/jpeg', fileSize: 12345 })
    mockBridge.parseImage.mockResolvedValue({ proposal: batchProposal })
    mockBridge.executeProposal.mockResolvedValue({ transaction_ids: ['t1', 't2'], undo_window_seconds: 0 })
  })

  it('输入行为 [语音][图片][输入框][发送] 顺序（T12-1 REQ-29 ②：图片钮移到语音旁）', () => {
    renderPage()
    const mic = screen.getByTestId('voice-mic-button')
    const img = screen.getByTestId('image-upload-button')
    const input = screen.getByPlaceholderText('输入记账短句…')
    // T12-1 重排：mic 在最左，img 紧挨其右，input 在 img 之后
    expect(mic.compareDocumentPosition(img) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(img.compareDocumentPosition(input) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(mic.compareDocumentPosition(input) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('完整链路：选图 → 解析 → 批量卡 → 确认 → executeProposal → 消息落定 + triggerSync', async () => {
    const user = userEvent.setup()
    renderPage()
    // 等 bookId 就绪
    await waitFor(() => expect(mockBridge.listBooks).toHaveBeenCalled())

    await user.click(screen.getByTestId('image-upload-button'))
    await waitFor(() => expect(mockBridge.pickImage).toHaveBeenCalledWith('gallery'))
    await waitFor(() => expect(mockBridge.parseImage).toHaveBeenCalledWith('att-1', 'book-1'))
    // picking/parsing 提示已消失，批量卡出现在消息流
    expect(screen.queryByTestId('image-status')).toBeNull()
    expect(screen.getByTestId('batch-confirmation-card')).toBeInTheDocument()

    await user.click(screen.getByTestId('batch-confirm'))
    await waitFor(() => expect(mockBridge.executeProposal).toHaveBeenCalledTimes(1))
    const [proposalArg, bookIdArg, token] = mockBridge.executeProposal.mock.calls[0]
    expect(bookIdArg).toBe('book-1')
    expect(String(token)).toMatch(/^chat_img_\d+$/)
    expect(proposalArg).toMatchObject({ action: 'add_tx_batch' })

    await waitFor(() => expect(mockAssistant.appendAssistantMessage).toHaveBeenCalledWith('已记录 2 笔交易'))
    expect(screen.queryByTestId('batch-confirmation-card')).toBeNull()
    expect(mockBridge.triggerSync).toHaveBeenCalled()
  })

  it('parse 失败 → 红条 + 重试；重试续 parse 不重新选图', async () => {
    mockBridge.parseImage
      .mockRejectedValueOnce(bridgeError('网关超时'))
      .mockResolvedValueOnce({ proposal: batchProposal })
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockBridge.listBooks).toHaveBeenCalled())

    await user.click(screen.getByTestId('image-upload-button'))
    await waitFor(() => expect(screen.getByTestId('image-error')).toHaveTextContent('网关超时'))
    expect(screen.queryByTestId('batch-confirmation-card')).toBeNull()

    await user.click(screen.getByTestId('image-retry'))
    await waitFor(() => expect(mockBridge.parseImage).toHaveBeenCalledTimes(2))
    // pick 仍只有第一次
    expect(mockBridge.pickImage).toHaveBeenCalledTimes(1)
    expect(mockBridge.parseImage.mock.calls[1]).toEqual(['att-1', 'book-1'])
    await waitFor(() => expect(screen.getByTestId('batch-confirmation-card')).toBeInTheDocument())
  })

  it('parse 503 no_ai_provider → AI 未配置专属文案（T12-1 决策 #9）', async () => {
    mockBridge.parseImage.mockRejectedValueOnce(
      bridgeError('AI service is temporarily unavailable', 'no_ai_provider'),
    )
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockBridge.listBooks).toHaveBeenCalled())

    await user.click(screen.getByTestId('image-upload-button'))
    const bar = await screen.findByTestId('image-error')
    expect(bar.textContent).toContain('AI_PROVIDER_API_KEY')
    expect(bar.textContent).not.toContain('AI service is temporarily unavailable')
  })

  it('用户取消选图 → 静默回 idle，无红条', async () => {
    mockBridge.pickImage.mockRejectedValueOnce(bridgeError('用户取消', 'cancelled'))
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockBridge.listBooks).toHaveBeenCalled())

    await user.click(screen.getByTestId('image-upload-button'))
    await waitFor(() => expect(mockBridge.pickImage).toHaveBeenCalled())
    expect(screen.queryByTestId('image-error')).toBeNull()
    expect(screen.getByTestId('image-upload-button')).toBeEnabled()
  })

  it('账本未就绪 → 红条「账本未就绪」，不发起选图', async () => {
    mockBridge.listBooks.mockResolvedValue([])
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockBridge.listBooks).toHaveBeenCalled())

    await user.click(screen.getByTestId('image-upload-button'))
    expect(screen.getByTestId('image-error')).toHaveTextContent('账本未就绪')
    expect(mockBridge.pickImage).not.toHaveBeenCalled()
  })

  it('批量卡显示期间文本输入不禁用（与单卡链路互不干扰，决策 #11）', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockBridge.listBooks).toHaveBeenCalled())
    await user.click(screen.getByTestId('image-upload-button'))
    await waitFor(() => expect(screen.getByTestId('batch-confirmation-card')).toBeInTheDocument())

    const input = screen.getByPlaceholderText('输入记账短句…')
    expect(input).toBeEnabled()
    await user.type(input, '午饭 12.5')
    expect(input).toHaveValue('午饭 12.5')
    // 批量卡仍在
    expect(screen.getByTestId('batch-confirmation-card')).toBeInTheDocument()
    // 🖼️ 按钮在 review 期间禁用（防重入）
    expect(screen.getByTestId('image-upload-button')).toBeDisabled()
  })

  it('executeProposal 失败 → 红条可见，卡片保留可重试确认', async () => {
    mockBridge.executeProposal.mockRejectedValueOnce(bridgeError('HTTP 409 冲突'))
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockBridge.listBooks).toHaveBeenCalled())
    await user.click(screen.getByTestId('image-upload-button'))
    await waitFor(() => expect(screen.getByTestId('batch-confirmation-card')).toBeInTheDocument())

    await user.click(screen.getByTestId('batch-confirm'))
    await waitFor(() => expect(screen.getByTestId('image-error')).toHaveTextContent('HTTP 409 冲突'))
    // 批量卡保留（review 态不收起）
    expect(screen.getByTestId('batch-confirmation-card')).toBeInTheDocument()
    expect(mockAssistant.appendAssistantMessage).not.toHaveBeenCalled()
  })

  // ── T14-3（REQ-40）：缺分类拦截 + 错误本地化 ──

  const noCatProposal = {
    ...batchProposal,
    transactions: [
      { type: 'expense', amountCents: 1200, categoryId: 'c1', bizDate: '2026-09-09', note: '米饭' },
      // 服务端「宁缺勿错」：未匹配行不带 categoryId 键
      { type: 'expense', amountCents: 300, bizDate: '2026-09-09', note: '可乐' },
    ],
  }

  it('提案缺分类 → 确认不发 executeProposal，红条中文引导卡内选择（T14-3）', async () => {
    mockBridge.parseImage.mockResolvedValueOnce({ proposal: noCatProposal })
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockBridge.listBooks).toHaveBeenCalled())
    await user.click(screen.getByTestId('image-upload-button'))
    await waitFor(() => expect(screen.getByTestId('batch-confirmation-card')).toBeInTheDocument())

    await user.click(screen.getByTestId('batch-confirm'))
    await waitFor(() => expect(screen.getByTestId('image-error')).toHaveTextContent('有记录未识别到分类'))
    expect(mockBridge.executeProposal).not.toHaveBeenCalled()
    // 卡片保留可修改后重试
    expect(screen.getByTestId('batch-confirmation-card')).toBeInTheDocument()
  })

  it('execute 错误 code=network → IMAGE_ERROR_ZH 中文条，英文原文不透出（T14-3）', async () => {
    mockBridge.executeProposal.mockRejectedValueOnce(
      bridgeError('Server error: HTTP 500: Internal database error', 'network'),
    )
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockBridge.listBooks).toHaveBeenCalled())
    await user.click(screen.getByTestId('image-upload-button'))
    await waitFor(() => expect(screen.getByTestId('batch-confirmation-card')).toBeInTheDocument())

    await user.click(screen.getByTestId('batch-confirm'))
    const bar = await screen.findByTestId('image-error')
    expect(bar.textContent).toContain('网络错误')
    expect(bar.textContent).not.toContain('Internal database error')
    expect(bar.textContent).not.toContain('Server error')
  })

  it('execute 错误 code=ai_missing_category → 中文分类引导条（T14-3 防御映射）', async () => {
    mockBridge.executeProposal.mockRejectedValueOnce(
      bridgeError('有记录未识别到分类，请在卡片中选择分类后重试', 'ai_missing_category'),
    )
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockBridge.listBooks).toHaveBeenCalled())
    await user.click(screen.getByTestId('image-upload-button'))
    await waitFor(() => expect(screen.getByTestId('batch-confirmation-card')).toBeInTheDocument())

    await user.click(screen.getByTestId('batch-confirm'))
    await waitFor(() =>
      expect(screen.getByTestId('image-error')).toHaveTextContent('有记录未识别到分类'),
    )
  })

  it('卡内选分类补齐后确认 → executeProposal 正常提交（T14-3 手选链路）', async () => {
    mockBridge.listCategories.mockResolvedValueOnce([
      { id: 'c9', kind: 'expense', name: '零食', parentId: null, version: 1 },
    ])
    mockBridge.parseImage.mockResolvedValueOnce({ proposal: noCatProposal })
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockBridge.listBooks).toHaveBeenCalled())
    await user.click(screen.getByTestId('image-upload-button'))
    await waitFor(() => expect(screen.getByTestId('batch-confirmation-card')).toBeInTheDocument())

    // 先直接确认 → 拦截
    await user.click(screen.getByTestId('batch-confirm'))
    await waitFor(() => expect(screen.getByTestId('image-error')).toHaveTextContent('有记录未识别到分类'))

    // 第二行展开编辑 → 选「零食」补齐分类 → 再确认 → 放行
    const editButtons = screen.getAllByText('编辑')
    await user.click(editButtons[1])
    await user.click(await screen.findByText('零食'))
    await user.click(screen.getByTestId('batch-confirm'))
    await waitFor(() => expect(mockBridge.executeProposal).toHaveBeenCalledTimes(1))
    const [proposalArg] = mockBridge.executeProposal.mock.calls[0]
    const txs = (proposalArg as { transactions: Array<Record<string, unknown> > }).transactions
    expect(txs[1].categoryId).toBe('c9')
    await waitFor(() => expect(mockAssistant.appendAssistantMessage).toHaveBeenCalledWith('已记录 2 笔交易'))
  })

  it('取消批量卡 → 收起回 idle', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockBridge.listBooks).toHaveBeenCalled())
    await user.click(screen.getByTestId('image-upload-button'))
    await waitFor(() => expect(screen.getByTestId('batch-confirmation-card')).toBeInTheDocument())

    await user.click(screen.getByTestId('batch-cancel'))
    expect(screen.queryByTestId('batch-confirmation-card')).toBeNull()
    expect(screen.getByTestId('image-upload-button')).toBeEnabled()
  })

  // T13-3（REQ-47 / 决策 #9）：底部撤销条已删除——卡内「撤销」常驻，底部条不渲染
  it('记账卡 committed → 卡内「撤销」钮在位，底部无撤销条', async () => {
    mockAssistant.activeProposal = {
      proposalId: 'prop-undo',
      action: 'add_tx',
      confidence: 0.9,
      requiresConfirmation: false,
      message: '已记录',
      warnings: [],
      transactions: [{
        type: 'expense',
        amountCents: 4500,
        categoryId: 'c1',
        bizDate: '2026-09-08',
        note: '午饭',
      }],
    }
    mockAssistant.cardStatus = 'committed'
    renderPage()
    await waitFor(() => expect(mockBridge.listBooks).toHaveBeenCalled())
    expect(screen.getByTestId('card-undo')).toBeInTheDocument()
    expect(screen.queryByText(/内可撤销/)).toBeNull()
    mockAssistant.activeProposal = null
    mockAssistant.cardStatus = null
  })
})
