/**
 * 第八轮 Bug-6/T8-6: use-assistant 免确认本地记账测试。
 *
 * 验证：
 * - 解析成功 → 直接 bridge.insertTransaction 本地写入（不调 executeProposal）
 * - 编辑 → softDelete 旧 + insert 新（替换写）
 * - 撤销 → softDelete 全部 + 卡片关闭
 * - bookId 缺失 / 写入失败 → 错误可见
 * - 非今日 bizDate → occurredAt 取该日本地正午（决策 #5 对齐）
 * - chat 提案 → 不写库不弹卡
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useAssistant, AI_OFFLINE_CANNED } from '../../features/assistant/use-assistant'
import { useAssistantChatStore } from '../../platform/store/assistant-chat-store'
import { parseInput } from '../../domain/nlu/parser'
import type { ActionProposal } from '../../domain/action-proposal'
import { todayBizDate } from '../../domain/date'

const mockBridge = vi.hoisted(() => ({
  insertTransaction: vi.fn().mockResolvedValue({ id: 'tx-1', version: 1 }),
  // T12-11（REQ-37）：edit/delete 意图执行与本地圈定用
  updateTransaction: vi.fn().mockResolvedValue({ id: 'tx-1', version: 2 }),
  listTransactionsByBook: vi.fn().mockResolvedValue([]),
  softDeleteTransaction: vi.fn().mockResolvedValue({ success: true }),
  triggerSync: vi.fn().mockResolvedValue(undefined),
  executeProposal: vi.fn(),
  validateProposal: vi.fn().mockResolvedValue({ valid: true }),
  interpretAI: vi.fn(),
  isAvailable: vi.fn().mockReturnValue(false),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

vi.mock('../../domain/nlu/parser', () => ({
  parseInput: vi.fn(),
}))

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

const TZ = 'Asia/Shanghai'

function txProposal(overrides: Partial<ActionProposal> = {}): ActionProposal {
  return {
    proposalId: 'prop-1',
    action: 'add_tx',
    confidence: 0.9,
    requiresConfirmation: true,
    message: '记下这笔',
    warnings: [],
    transactions: [{
      type: 'expense',
      amountCents: 67800,
      categoryId: 'cat-food',
      bizDate: todayBizDate(TZ),
      note: '吃饭',
    }],
    ...overrides,
  }
}

function setup(bookId: string | null = 'book-1', defaultAccountId: string | null = null) {
  return renderHook(() => useAssistant({ categories: [], bookId, defaultAccountId, timezone: TZ }))
}

describe('use-assistant — 免确认本地记账（T8-6）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.insertTransaction.mockResolvedValue({ id: 'tx-1', version: 1 })
    mockBridge.softDeleteTransaction.mockResolvedValue({ success: true })
    mockBridge.isAvailable.mockReturnValue(false)
    // T14-4：messages 外置到模块级 store——逐用例复位防串
    useAssistantChatStore.setState({ messages: [] })
  })

  it('解析成功直接本地写入，不调用 executeProposal，卡片进入 committed + 撤销窗', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    const { result } = setup()

    await act(async () => { await result.current.submitInput('吃饭678') })

    await waitFor(() => expect(result.current.cardStatus).toBe('committed'))
    expect(mockBridge.executeProposal).not.toHaveBeenCalled()
    expect(mockBridge.insertTransaction).toHaveBeenCalledTimes(1)

    const [tx, mutationId] = mockBridge.insertTransaction.mock.calls[0]
    expect(tx.bookId).toBe('book-1')
    expect(tx.amountCents).toBe(67800)
    expect(tx.categoryId).toBe('cat-food')
    expect(tx.source).toBe('nlu_ai')
    expect(tx.id).toMatch(UUID_RE)
    expect(mutationId).toMatch(UUID_RE)

    // 今日 → occurredAt ≈ now
    expect(Date.now() - tx.occurredAt).toBeLessThan(5_000)

    const last = result.current.messages[result.current.messages.length - 1]
    expect(last.proposalStatus).toBe('committed')
    expect(last.transactionIds).toHaveLength(1)
    expect(last.text).toBe('已记录 1 笔交易')
    // 卡片常驻（卡内「撤销」钮常驻可用，T13-3：无底部撤销条）
    expect(result.current.activeProposal).not.toBeNull()
    expect(result.current.phase).toBe('idle')
    expect(mockBridge.triggerSync).toHaveBeenCalled()
  })

  it('非今日 bizDate → occurredAt 取该日本地正午（决策 #5 对齐）', async () => {
    const p = txProposal()
    p.transactions![0].bizDate = '2026-09-01'
    vi.mocked(parseInput).mockReturnValue(p)
    const { result } = setup()

    await act(async () => { await result.current.submitInput('吃饭678') })

    const [tx] = mockBridge.insertTransaction.mock.calls[0]
    expect(tx.bizDate).toBe('2026-09-01')
    expect(tx.occurredAt).toBe(new Date('2026-09-01T12:00:00').getTime())
  })

  it('bookId 缺失 → 明确报错且不写入', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    const { result } = setup(null)

    await act(async () => { await result.current.submitInput('吃饭678') })

    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
    expect(result.current.activeProposal).toBeNull()
    expect(result.current.phase).toBe('idle')
    const last = result.current.messages[result.current.messages.length - 1]
    expect(last.text).toContain('账本未就绪')
  })

  it('编辑 = softDelete 旧笔 + insert 新值（替换写，无 update 命令）', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    const { result } = setup()

    await act(async () => { await result.current.submitInput('吃饭678') })
    await waitFor(() => expect(result.current.cardStatus).toBe('committed'))
    const writtenId = mockBridge.insertTransaction.mock.calls[0][0].id as string

    const edited = txProposal()
    edited.transactions![0].amountCents = 5000
    edited.transactions![0].note = '午饭'
    await act(async () => { await result.current.editProposal(edited) })

    expect(mockBridge.softDeleteTransaction).toHaveBeenCalledTimes(1)
    expect(mockBridge.softDeleteTransaction.mock.calls[0][0]).toBe(writtenId)
    expect(mockBridge.softDeleteTransaction.mock.calls[0][1]).toMatch(UUID_RE)
    expect(mockBridge.insertTransaction).toHaveBeenCalledTimes(2)
    const [tx2] = mockBridge.insertTransaction.mock.calls[1]
    expect(tx2.amountCents).toBe(5000)
    expect(tx2.note).toBe('午饭')

    await waitFor(() => {
      const m = result.current.messages.find(x => x.proposal?.proposalId === 'prop-1')
      expect(m?.text).toBe('已更新记录')
      expect(m?.proposalStatus).toBe('committed')
    })
    // 编辑后撤销窗不重启（编辑立即生效）
    expect(result.current.phase).toBe('idle')
  })

  it('撤销 → softDelete 已写入交易并关闭卡片', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    const { result } = setup()

    await act(async () => { await result.current.submitInput('吃饭678') })
    await waitFor(() => expect(result.current.cardStatus).toBe('committed'))
    const writtenId = mockBridge.insertTransaction.mock.calls[0][0].id as string

    await act(async () => { await result.current.undoExecution() })

    expect(mockBridge.softDeleteTransaction).toHaveBeenCalledWith(writtenId, expect.any(String))
    expect(result.current.activeProposal).toBeNull()
    expect(result.current.cardStatus).toBeNull()
    const m = result.current.messages.find(x => x.proposal?.proposalId === 'prop-1')
    expect(m?.proposalStatus).toBe('undone')
    expect(m?.text).toBe('已撤销')
  })

  it('本地写入失败 → error 可见，卡片保留可编辑重试', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    mockBridge.insertTransaction.mockRejectedValueOnce(new Error('db down'))
    const { result } = setup()

    await act(async () => { await result.current.submitInput('吃饭678') })

    expect(result.current.cardStatus).toBe('error')
    expect(result.current.activeProposal).not.toBeNull()
    expect(result.current.phase).toBe('idle')
    const last = result.current.messages[result.current.messages.length - 1]
    expect(last.text).toContain('❌ 记账失败：db down')
  })

  it('chat 提案且云端不可达 → 统一降级 canned（T12-11 REQ-37 ④）', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal({
      action: 'chat',
      transactions: undefined,
      message: '你好呀',
    }))
    const { result } = setup() // isAvailable=false → AI 不触达

    await act(async () => { await result.current.submitInput('你好') })

    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
    expect(result.current.activeProposal).toBeNull()
    const last = result.current.messages[result.current.messages.length - 1]
    expect(last.text).toBe(AI_OFFLINE_CANNED)
    expect(result.current.phase).toBe('idle')
  })

  // ─── T9-7（REQ-15）：默认账户填充与账户编辑链路 ───

  it('提案未带账户 → defaultAccountId 兜底进 insertTransaction payload', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    const { result } = setup('book-1', 'acc-def')

    await act(async () => { await result.current.submitInput('吃饭678') })
    await waitFor(() => expect(result.current.cardStatus).toBe('committed'))

    const [tx] = mockBridge.insertTransaction.mock.calls[0]
    expect(tx.accountId).toBe('acc-def')
  })

  it('提案自带账户 → 优先于 defaultAccountId', async () => {
    const p = txProposal()
    p.transactions![0].accountId = 'acc-own'
    vi.mocked(parseInput).mockReturnValue(p)
    const { result } = setup('book-1', 'acc-def')

    await act(async () => { await result.current.submitInput('吃饭678') })
    await waitFor(() => expect(result.current.cardStatus).toBe('committed'))

    const [tx] = mockBridge.insertTransaction.mock.calls[0]
    expect(tx.accountId).toBe('acc-own')
  })

  // T16-7（收口修复）：草稿提案未点名账户时，兜底值只存在于写入 payload 里，
  // 消息内提案仍是空白 → 只读历史卡只能按「页面默认账户」猜，而该默认在卡片挂载
  // 时可能尚未就绪，会显示「不使用账户」（与库内实际写入不符）。修复=回填。
  it('T16-7：未点名账户 → 消息内提案回填实际写入账户（历史卡与落库一致）', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    const { result } = setup('book-1', 'acc-def')

    await act(async () => { await result.current.submitInput('吃饭678') })
    await waitFor(() => expect(result.current.cardStatus).toBe('committed'))

    const msg = useAssistantChatStore.getState().messages.find(m => m.proposal)
    // 本用例走的是本地解析（v1 车道）——`AnyProposal` 是两条车道的并集，
    // 这里明确收窄到 v1 再看 `transactions`（v2 没有那个根字段）。
    expect((msg?.proposal as ActionProposal | undefined)?.transactions?.[0].accountId)
      .toBe('acc-def')
  })

  it('T16-7：编辑链路同样回填（换账户后消息内提案 = 实际写入账户）', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    const { result } = setup('book-1', 'acc-def')

    await act(async () => { await result.current.submitInput('吃饭678') })
    await waitFor(() => expect(result.current.cardStatus).toBe('committed'))

    const edited = txProposal()
    edited.transactions![0].accountId = 'acc-new'
    await act(async () => { await result.current.editProposal(edited) })

    const msg = useAssistantChatStore.getState().messages.find(m => m.proposal)
    expect((msg?.proposal as ActionProposal | undefined)?.transactions?.[0].accountId)
      .toBe('acc-new')
  })

  it('卡编辑换账户 → softDelete 旧 + insert 新且 accountId 更新', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    const { result } = setup('book-1', 'acc-def')

    await act(async () => { await result.current.submitInput('吃饭678') })
    await waitFor(() => expect(result.current.cardStatus).toBe('committed'))
    const writtenId = mockBridge.insertTransaction.mock.calls[0][0].id as string

    const edited = txProposal()
    edited.transactions![0].accountId = 'acc-new'
    await act(async () => { await result.current.editProposal(edited) })

    expect(mockBridge.softDeleteTransaction).toHaveBeenCalledTimes(1)
    expect(mockBridge.softDeleteTransaction.mock.calls[0][0]).toBe(writtenId)
    const [tx2] = mockBridge.insertTransaction.mock.calls[1]
    expect(tx2.accountId).toBe('acc-new')
  })

  it('编辑显式选「不使用账户」（null）→ 落 null，不被默认账户重新填充', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    const { result } = setup('book-1', 'acc-def')

    await act(async () => { await result.current.submitInput('吃饭678') })
    await waitFor(() => expect(result.current.cardStatus).toBe('committed'))
    expect(mockBridge.insertTransaction.mock.calls[0][0].accountId).toBe('acc-def')

    const edited = txProposal()
    edited.transactions![0].accountId = null
    await act(async () => { await result.current.editProposal(edited) })

    const [tx2] = mockBridge.insertTransaction.mock.calls[1]
    expect(tx2.accountId).toBeNull()
  })

  it('defaultAccountId 为 null 且提案无账户 → payload accountId null（无账户用户不受影响）', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    const { result } = setup('book-1', null)

    await act(async () => { await result.current.submitInput('吃饭678') })
    await waitFor(() => expect(result.current.cardStatus).toBe('committed'))

    const [tx] = mockBridge.insertTransaction.mock.calls[0]
    expect(tx.accountId).toBeNull()
  })

  // ─── 第十一轮 T11-6（REQ-27 / 决策 #7）：用户画像注入在线 AI 路径 ───
  // ─── 第十二轮 T12-11（REQ-37）：context 恒带 today（相对日期换算基准），
  //     画像/角色/categories 缺段传 null，服务端渲染段跳过 ───

  function setupWithProfile(profile: Record<string, string> | null) {
    return renderHook(() => useAssistant({
      categories: [], bookId: 'book-1', defaultAccountId: null, timezone: TZ, profile,
    }))
  }

  const baseCtx = {
    profile: null, persona: null, categories: null, today: todayBizDate(TZ),
    // T13-5（REQ-48 / 决策 #7）：对象名单四段——hook 未收到列表时传 null 段
    books: null, accounts: null, budgets: null, goals: null,
  }

  it('有画像 → interpretAI 收到 context.profile 段', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal({ confidence: 0.3 })) // 低置信 → 走 AI
    mockBridge.isAvailable.mockReturnValue(true)
    mockBridge.interpretAI.mockResolvedValue({
      output: { type: 'expense', amountCents: 1250, category: '餐饮', note: '午饭', bizDate: '', confidence: 0.9 },
    })
    const { result } = setupWithProfile({ nickname: '老板', communicationStyle: '简洁直接' })

    await act(async () => { await result.current.submitInput('午饭吃了十二块五') })

    expect(mockBridge.interpretAI).toHaveBeenCalledWith(
      '午饭吃了十二块五',
      { ...baseCtx, profile: { nickname: '老板', communicationStyle: '简洁直接' } },
    )
  })

  it('画像为 null → profile 段为 null（today 常驻）', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal({ confidence: 0.3 }))
    mockBridge.isAvailable.mockReturnValue(true)
    mockBridge.interpretAI.mockResolvedValue(undefined)
    const { result } = setupWithProfile(null)

    await act(async () => { await result.current.submitInput('随便说点什么') })

    expect(mockBridge.interpretAI).toHaveBeenCalledWith('随便说点什么', baseCtx)
  })

  it('画像为空对象 → profile 段传 null，不发空段', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal({ confidence: 0.3 }))
    mockBridge.isAvailable.mockReturnValue(true)
    mockBridge.interpretAI.mockResolvedValue(undefined)
    const { result } = setupWithProfile({})

    await act(async () => { await result.current.submitInput('随便说点什么') })

    expect(mockBridge.interpretAI).toHaveBeenCalledWith('随便说点什么', baseCtx)
  })

  it('本地 NLU 高置信路径不触 AI（context 管线与本地解析无关）', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal()) // confidence 0.9 → needsAI=false
    mockBridge.isAvailable.mockReturnValue(true)
    const { result } = setupWithProfile({ nickname: '老板' })

    await act(async () => { await result.current.submitInput('吃饭678') })

    expect(mockBridge.interpretAI).not.toHaveBeenCalled()
  })

  // ─── 第十一轮 T11-7（REQ-28 / 决策 #7/#8）：角色 persona 注入在线 AI 路径 ───

  function setupContext(
    profile: Record<string, string> | null,
    persona: { name: string; text: string } | null,
  ) {
    return renderHook(() => useAssistant({
      categories: [], bookId: 'book-1', defaultAccountId: null, timezone: TZ, profile, persona,
    }))
  }

  it('仅角色（无画像）→ context.persona 段携带，其余段 null', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal({ confidence: 0.3 }))
    mockBridge.isAvailable.mockReturnValue(true)
    mockBridge.interpretAI.mockResolvedValue(undefined)
    const persona = { name: '饭团', text: '幽默的吃货伙伴' }
    const { result } = setupContext(null, persona)

    await act(async () => { await result.current.submitInput('随便说点什么') })

    expect(mockBridge.interpretAI).toHaveBeenCalledWith(
      '随便说点什么',
      { ...baseCtx, persona },
    )
  })

  it('画像+角色并存 → context 两段同时携带', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal({ confidence: 0.3 }))
    mockBridge.isAvailable.mockReturnValue(true)
    mockBridge.interpretAI.mockResolvedValue(undefined)
    const profile = { nickname: '老板' }
    const persona = { name: '老陈', text: '严谨财务顾问' }
    const { result } = setupContext(profile, persona)

    await act(async () => { await result.current.submitInput('随便说点什么') })

    expect(mockBridge.interpretAI).toHaveBeenCalledWith(
      '随便说点什么',
      { ...baseCtx, profile, persona },
    )
  })

  it('画像空对象+无角色 → 各资料段传 null（不发空段；today 常驻）', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal({ confidence: 0.3 }))
    mockBridge.isAvailable.mockReturnValue(true)
    mockBridge.interpretAI.mockResolvedValue(undefined)
    const { result } = setupContext({}, null)

    await act(async () => { await result.current.submitInput('随便说点什么') })

    expect(mockBridge.interpretAI).toHaveBeenCalledWith(
      '随便说点什么',
      { ...baseCtx, profile: null, persona: null },
    )
  })

  // ─── 第十二轮 T12-8（REQ-30 / 决策 #7 #9）：缺分类待选态 + 云端失败轻提示 + 错误中文化 ───

  /** 「喝酒472」形态：add_tx 但 transactions[0] 无 categoryId，带 warning（复刻 REQ-30 场景）。 */
  function noCatProposal(): ActionProposal {
    return txProposal({
      confidence: 0.5,
      warnings: ['未识别到分类「喝酒」'],
      transactions: [{
        type: 'expense',
        amountCents: 47200,
        bizDate: todayBizDate(TZ),
        note: '喝酒',
      }],
    })
  }

  it('add_tx 缺分类 → 不自动写入，卡片进入 pending-category 态（消息 pending 未记账）', async () => {
    vi.mocked(parseInput).mockReturnValue(noCatProposal())
    const { result } = setup()

    await act(async () => { await result.current.submitInput('喝酒472') })

    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
    expect(result.current.cardStatus).toBe('pending-category')
    expect(result.current.activeProposal).not.toBeNull()
    expect(result.current.phase).toBe('idle')
    const m = result.current.messages.find(x => x.proposal?.proposalId === 'prop-1')
    expect(m?.proposalStatus).toBe('pending')
    expect(m?.text).toContain('未识别到分类')
  })

  it('待选态改金额但未选分类 → 仍不写库，态保持，草稿回写提案', async () => {
    vi.mocked(parseInput).mockReturnValue(noCatProposal())
    const { result } = setup()
    await act(async () => { await result.current.submitInput('喝酒472') })

    const edited = noCatProposal()
    edited.transactions![0].amountCents = 5000
    await act(async () => { await result.current.editProposal(edited) })

    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
    expect(result.current.cardStatus).toBe('pending-category')
    expect((result.current.activeProposal as ActionProposal | null)?.transactions?.[0].amountCents)
      .toBe(5000)
  })

  it('待选态选定分类 → 接管首次写入（无 softDelete），committed + 文案已记录', async () => {
    vi.mocked(parseInput).mockReturnValue(noCatProposal())
    const { result } = setup()
    await act(async () => { await result.current.submitInput('喝酒472') })
    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()

    const edited = noCatProposal()
    edited.transactions![0].categoryId = 'cat-food'
    await act(async () => { await result.current.editProposal(edited) })

    await waitFor(() => expect(result.current.cardStatus).toBe('committed'))
    expect(mockBridge.insertTransaction).toHaveBeenCalledTimes(1)
    expect(mockBridge.insertTransaction.mock.calls[0][0].categoryId).toBe('cat-food')
    expect(mockBridge.softDeleteTransaction).not.toHaveBeenCalled()
    const m = result.current.messages.find(x => x.proposal?.proposalId === 'prop-1')
    expect(m?.proposalStatus).toBe('committed')
    expect(m?.text).toBe('已记录 1 笔交易')
    expect(result.current.phase).toBe('idle')
  })

  it('interpretAI 失败 → 会话级一次性轻提示，第二次失败不再弹（决策 #9）', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal({ confidence: 0.3 }))
    mockBridge.isAvailable.mockReturnValue(true)
    mockBridge.interpretAI.mockRejectedValue(
      Object.assign(new Error('AI service unavailable'), { code: '503' }),
    )
    const { result } = setup()

    await act(async () => { await result.current.submitInput('午饭吃了十二块五') })
    await act(async () => { await result.current.submitInput('晚饭二十') })

    const notices = result.current.messages.filter(m => m.text.includes('云端 AI 暂时不可用'))
    expect(notices).toHaveLength(1)
    expect(notices[0].text).toContain('本次以本地解析继续')
    // 两次提交都回退本地提案正常写入
    expect(mockBridge.insertTransaction).toHaveBeenCalledTimes(2)
  })

  it('code=no_ai_provider → 决策 #9 配置指引专属文案', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal({ confidence: 0.3 }))
    mockBridge.isAvailable.mockReturnValue(true)
    mockBridge.interpretAI.mockRejectedValue(
      Object.assign(new Error('AI service is temporarily unavailable'), { code: 'no_ai_provider' }),
    )
    const { result } = setup()

    await act(async () => { await result.current.submitInput('午饭') })

    expect(result.current.messages.some(m =>
      m.text.includes('AI_PROVIDER_API_KEY'))).toBe(true)
  })

  it('Kotlin "entity IDs are required" → 错误可见为中文（决策 #7 兜底映射）', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    mockBridge.insertTransaction.mockRejectedValueOnce(new Error('entity IDs are required'))
    const { result } = setup()

    await act(async () => { await result.current.submitInput('吃饭678') })

    const last = result.current.messages[result.current.messages.length - 1]
    expect(last.text).toContain('缺少分类或账本信息')
    expect(last.text).not.toContain('entity IDs')
    expect(result.current.cardStatus).toBe('error')
  })
})

// ─── 第十二轮 T12-9（REQ-35）：卡内账本/预算 chip override 进写入链路 ───

describe('use-assistant — 编辑写入 overrides（T12-9）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.insertTransaction.mockResolvedValue({ id: 'tx-1', version: 1 })
    mockBridge.softDeleteTransaction.mockResolvedValue({ success: true })
    mockBridge.isAvailable.mockReturnValue(false)
    // T14-4：messages 外置到模块级 store——逐用例复位防串
    useAssistantChatStore.setState({ messages: [] })
  })

  /** 提交并等待 committed，返回后可直接走 editProposal。 */
  async function commitFirst(result: {
    current: { submitInput: (t: string) => Promise<unknown>; cardStatus: string | null }
  }) {
    await act(async () => { await result.current.submitInput('吃饭678') })
    await waitFor(() => expect(result.current.cardStatus).toBe('committed'))
  }

  it('editProposal 带 {bookId} → 替换写 insert 落新账本', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    const { result } = setup()
    await commitFirst(result)

    const edited = txProposal()
    edited.transactions![0].amountCents = 5000
    await act(async () => { await result.current.editProposal(edited, { bookId: 'book-2' }) })

    expect(mockBridge.softDeleteTransaction).toHaveBeenCalledTimes(1)
    const [tx2] = mockBridge.insertTransaction.mock.calls[1]
    expect(tx2.bookId).toBe('book-2')
    expect(tx2.amountCents).toBe(5000)
  })

  it('editProposal 带 {includeInBudget:false} → payload 显式不计入；不带 → 无该键（native 缺省）', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal())
    const { result } = setup()
    await commitFirst(result)

    const edited = txProposal()
    edited.transactions![0].note = '午饭'
    await act(async () => { await result.current.editProposal(edited, { includeInBudget: false }) })
    const [tx2] = mockBridge.insertTransaction.mock.calls[1]
    expect(tx2.includeInBudget).toBe(false)
    expect(tx2.bookId).toBe('book-1') // bookId 未覆盖 → hook 层账本

    const edited2 = txProposal()
    edited2.transactions![0].note = '晚饭'
    await act(async () => { await result.current.editProposal(edited2) })
    const [tx3] = mockBridge.insertTransaction.mock.calls[2]
    expect('includeInBudget' in tx3).toBe(false)
  })

  it('待选分类态选类+切账本一次提交 → 首次写入即用 override 账本（无 softDelete）', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal({
      confidence: 0.5,
      warnings: ['未识别到分类「喝酒」'],
      transactions: [{ type: 'expense', amountCents: 47200, bizDate: todayBizDate(TZ), note: '喝酒' }],
    }))
    const { result } = setup()
    await act(async () => { await result.current.submitInput('喝酒472') })
    expect(result.current.cardStatus).toBe('pending-category')

    const edited = txProposal({
      confidence: 0.5,
      warnings: ['未识别到分类「喝酒」'],
      transactions: [{ type: 'expense', amountCents: 47200, bizDate: todayBizDate(TZ), note: '喝酒', categoryId: 'cat-food' }],
    })
    await act(async () => { await result.current.editProposal(edited, { bookId: 'book-2' }) })

    await waitFor(() => expect(result.current.cardStatus).toBe('committed'))
    expect(mockBridge.softDeleteTransaction).not.toHaveBeenCalled()
    expect(mockBridge.insertTransaction).toHaveBeenCalledTimes(1)
    expect(mockBridge.insertTransaction.mock.calls[0][0].bookId).toBe('book-2')
    expect(mockBridge.insertTransaction.mock.calls[0][0].categoryId).toBe('cat-food')
  })
})
