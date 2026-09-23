/**
 * 第十二轮 T12-11（REQ-37 / 决策 #2 #3 #5）：use-assistant intent v2 分发矩阵测试。
 *
 * 覆盖：
 * - chat/query → 文本气泡（query 本地直算优先、零命中 canned、无 filter 转述 reply）
 * - edit/delete → filter 本地圈定 pendingOp；确认前零写入
 * - confirmOp → 逐笔 update/softDelete（UUID mutationId、version+1、set 覆盖）；
 *   部分失败/全失败/50 笔截断回执；成功 reply 优先、模板兜底
 * - 无范围守卫 / 取消 / 新输入作废 / bookkeeping（含老形状）走现链路
 * - 降级 canned（AI_OFFLINE_CANNED）与 context.categories/today 注入
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useAssistant, AI_OFFLINE_CANNED } from '../../features/assistant/use-assistant'
import { INTERRUPTED_TURN_NOTE } from '../../features/assistant/history-projection'
import { resetCloudReachabilityForTest } from '../../platform/cloud-reachability'
import { useAssistantChatStore } from '../../platform/store/assistant-chat-store'
import { parseInput } from '../../domain/nlu/parser'
import type { ActionProposal } from '../../domain/action-proposal'
import type { AccountData, BookData, TransactionData } from '../../platform/bridge/bridge-types'
import { todayBizDate } from '../../domain/date'

const mockBridge = vi.hoisted(() => ({
  insertTransaction: vi.fn().mockResolvedValue({ id: 'tx-1', version: 1 }),
  updateTransaction: vi.fn().mockResolvedValue({ id: 'tx-1', version: 8 }),
  listTransactionsByBook: vi.fn().mockResolvedValue([]),
  softDeleteTransaction: vi.fn().mockResolvedValue({ success: true }),
  triggerSync: vi.fn().mockResolvedValue(undefined),
  // T18-2（REQ-67①）：写后走**阻塞式真同步**（auth.triggerSync），不再是入队式 triggerSync
  triggerAuthSync: vi.fn().mockResolvedValue({ outcome: 'OK' }),
  executeProposal: vi.fn(),
  validateProposal: vi.fn().mockResolvedValue({ valid: true }),
  interpretAI: vi.fn(),
  isAvailable: vi.fn().mockReturnValue(false),
  // T18-8（REQ-71）：`auth.me` = 可达性探测的探针；resolve = 云端可达
  getMe: vi.fn().mockResolvedValue({}),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

vi.mock('../../domain/nlu/parser', () => ({
  parseInput: vi.fn(),
}))

// T18-8：可达性探测是模块级单例（带 30s 缓存）——逐用例复位，否则上一条用例的
// 「在线」判定会漏进下一条。
beforeEach(() => { resetCloudReachabilityForTest() })

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const TZ = 'Asia/Shanghai'

const cats = [
  { id: 'c-food', kind: 'expense' as const, name: '餐饮', parentId: null, version: 1 },
  { id: 'c-car', kind: 'expense' as const, name: '交通', parentId: null, version: 1 },
]

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

function opTx(partial: Partial<TransactionData> & { id: string }): TransactionData {
  return {
    bookId: 'book-1', type: 'expense', amountCents: 2300, categoryId: 'c-car',
    accountId: null, note: '打车', bizDate: '2026-09-08', occurredAt: 1,
    source: 'manual', version: 7, deletedAt: null, ...partial,
  }
}

function setupOp(opts: { aiCategories?: string[] | null } = {}) {
  return renderHook(() => useAssistant({
    categories: cats, bookId: 'book-1', defaultAccountId: null, timezone: TZ,
    aiCategories: opts.aiCategories ?? null,
  }))
}

/** 走 AI 路径的环境：桥可用 + 本地低置信（needsAI）+ interpretAI 固定输出。 */
function aiPath(output: Record<string, unknown> | null) {
  vi.mocked(parseInput).mockReturnValue(txProposal({ confidence: 0.3 }))
  mockBridge.isAvailable.mockReturnValue(true)
  mockBridge.interpretAI.mockResolvedValue(output ? { output } : undefined)
}

const lastMsg = (result: { current: { messages: { text: string }[] } }) =>
  result.current.messages[result.current.messages.length - 1]

describe('use-assistant — AI 意图分发矩阵（T12-11）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.insertTransaction.mockResolvedValue({ id: 'tx-1', version: 1 })
    mockBridge.updateTransaction.mockResolvedValue({ id: 'tx-1', version: 8 })
    mockBridge.softDeleteTransaction.mockResolvedValue({ success: true })
    mockBridge.listTransactionsByBook.mockResolvedValue([])
    mockBridge.triggerAuthSync.mockResolvedValue({ outcome: 'OK' })
    mockBridge.isAvailable.mockReturnValue(false)
    // T14-4：messages 外置到模块级 store——逐用例复位防串
    useAssistantChatStore.setState({ messages: [] })
  })

  it('chat → reply 落文本气泡，不写库不开卡', async () => {
    aiPath({
      intent: 'chat', type: '', amountCents: 0, category: '', note: '', bizDate: '',
      confidence: 0.9, reply: '你好呀，我是小贝',
    })
    const { result } = setupOp()

    await act(async () => { await result.current.submitInput('你好') })

    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
    expect(result.current.pendingOp).toBeNull()
    expect(result.current.activeProposal).toBeNull()
    expect(lastMsg(result).text).toBe('你好呀，我是小贝')
    expect(result.current.phase).toBe('idle')
  })

  // ─── T13-6（REQ-49 / 决策 #10）：人设守卫 —— 闲聊被云端误判 bookkeeping ───

  it('守卫：bookkeeping 误判无金额 → chat 气泡转述 reply，不开记账提案卡', async () => {
    aiPath({
      intent: 'bookkeeping', type: '', amountCents: 0, category: '', note: '',
      bizDate: '', confidence: 0.8, reply: '你好呀，我是你的记账伙伴',
    })
    const { result } = setupOp()

    await act(async () => { await result.current.submitInput('你是谁') })

    expect(result.current.activeProposal).toBeNull()
    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
    expect(lastMsg(result).text).toBe('你好呀，我是你的记账伙伴')
  })

  it('守卫：改判后 reply 空 → 人设化兜底句（含角色名，非「无法识别金额」系统腔）', async () => {
    aiPath({
      intent: 'bookkeeping', type: '', amountCents: 0, category: '', note: '',
      bizDate: '', confidence: 0.8, reply: '',
    })
    const { result } = renderHook(() => useAssistant({
      categories: cats, bookId: 'book-1', defaultAccountId: null, timezone: TZ,
      persona: { name: '冷面管家', text: '惜字如金的管家' },
    }))

    await act(async () => { await result.current.submitInput('你是谁') })

    expect(result.current.activeProposal).toBeNull()
    expect(lastMsg(result).text).toContain('冷面管家')
    expect(lastMsg(result).text).not.toContain('无法识别金额')
  })

  it('query 有 filter → 本地直算优先（不转述 LLM 编造数字）', async () => {
    aiPath({ intent: 'query', reply: '我帮你查一下', filter: { category: '餐饮' } })
    mockBridge.listTransactionsByBook.mockResolvedValue([
      opTx({ id: 'a', categoryId: 'c-food', amountCents: 1250 }),
      opTx({ id: 'b', categoryId: 'c-food', amountCents: 1000 }),
      opTx({ id: 'c', categoryId: 'c-car' }), // 非餐饮，不计
    ])
    const { result } = setupOp()

    await act(async () => { await result.current.submitInput('这个月吃饭花了多少') })

    expect(lastMsg(result).text)
      .toBe('查询到 2 笔记录：支出 ¥22.50，收入 ¥0.00，净额 ¥-22.50')
    expect(result.current.pendingOp).toBeNull()
  })

  it('query 本地命中 0 笔 → 「没找到」canned', async () => {
    aiPath({ intent: 'query', reply: '查到了吗', filter: { keyword: '不存在' } })
    mockBridge.listTransactionsByBook.mockResolvedValue([opTx({ id: 'a' })])
    const { result } = setupOp()

    await act(async () => { await result.current.submitInput('去年买过啥') })

    expect(lastMsg(result).text).toBe('没找到符合条件的记录')
  })

  it('query 无 filter → 转述 reply（本地无从直算）', async () => {
    aiPath({ intent: 'query', reply: '这个要看你自己的账本哦' })
    const { result } = setupOp()

    await act(async () => { await result.current.submitInput('我富裕吗') })

    expect(lastMsg(result).text).toBe('这个要看你自己的账本哦')
    expect(mockBridge.listTransactionsByBook).not.toHaveBeenCalled()
  })

  it('query 无 filter 但 reply 编造金额 → 不转述，换引导语（T12-12 数值守卫）', async () => {
    aiPath({ intent: 'query', reply: '这个月目前的总支出是358.5元。需要我帮你看看吗？' })
    const { result } = setupOp()

    await act(async () => { await result.current.submitInput('这个月花了多少') })

    expect(lastMsg(result).text).not.toContain('358.5')
    expect(lastMsg(result).text).toContain('从账本里直接算')
  })

  it('edit → filter 本地圈定 pendingOp，确认前零写入', async () => {
    aiPath({
      intent: 'edit', reply: '好的，帮你改',
      filter: { keyword: '打车' }, set: { amountCents: 500 },
    })
    mockBridge.listTransactionsByBook.mockResolvedValue([
      opTx({ id: 'a' }), opTx({ id: 'b', note: '地铁' }),
    ])
    const { result } = setupOp()

    await act(async () => { await result.current.submitInput('把打车那笔改成5块') })

    expect(mockBridge.updateTransaction).not.toHaveBeenCalled()
    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
    expect(result.current.pendingOp).toMatchObject({ kind: 'edit', reply: '好的，帮你改' })
    expect(result.current.pendingOp!.candidates.map(t => t.id)).toEqual(['a'])
  })

  it('edit 执行 → updateTransaction 保 id/账本/账户、version+1、set 覆盖、UUID mutationId', async () => {
    aiPath({
      intent: 'edit', reply: '好',
      filter: { keyword: '打车' }, set: { amountCents: 500, note: '顺风车' },
    })
    mockBridge.listTransactionsByBook.mockResolvedValue([opTx({ id: 'a', accountId: 'acc-1' })])
    const { result } = setupOp()
    await act(async () => { await result.current.submitInput('把打车那笔改成5块') })

    await act(async () => { await result.current.confirmOp() })

    expect(mockBridge.updateTransaction).toHaveBeenCalledTimes(1)
    const [input, mutationId] = mockBridge.updateTransaction.mock.calls[0]
    expect(input).toMatchObject({
      id: 'a', bookId: 'book-1', accountId: 'acc-1', categoryId: 'c-car',
      amountCents: 500, note: '顺风车', bizDate: '2026-09-08',
      source: 'manual', version: 8, // 乐观锁：当前 7 + 1（T12-7）
    })
    expect(mutationId).toMatch(UUID_RE)
    expect(result.current.pendingOp).toBeNull()
    // 全成功无截断 → 回执优先云端 reply；有写入才跑写后真同步（T18-2）
    expect(lastMsg(result).text).toBe('好')
    expect(mockBridge.triggerAuthSync).toHaveBeenCalledTimes(1)
  })

  it('delete 执行 → 逐笔 softDelete（各自 UUID），reply 空走模板回执', async () => {
    aiPath({ intent: 'delete', reply: '', filter: { category: '交通' } })
    mockBridge.listTransactionsByBook.mockResolvedValue([
      opTx({ id: 'a' }), opTx({ id: 'b', bizDate: '2026-09-01' }),
      opTx({ id: 'c', categoryId: 'c-food' }),
    ])
    const { result } = setupOp()
    await act(async () => { await result.current.submitInput('删掉所有交通支出') })
    expect(result.current.pendingOp!.candidates.map(t => t.id)).toEqual(['a', 'b'])

    await act(async () => { await result.current.confirmOp() })

    expect(mockBridge.softDeleteTransaction).toHaveBeenCalledTimes(2)
    expect(mockBridge.softDeleteTransaction.mock.calls[0][0]).toBe('a')
    expect(mockBridge.softDeleteTransaction.mock.calls[0][1]).toMatch(UUID_RE)
    expect(mockBridge.softDeleteTransaction.mock.calls[1][0]).toBe('b')
    expect(mockBridge.updateTransaction).not.toHaveBeenCalled()
    expect(lastMsg(result).text).toBe('已删除 2 笔')
  })

  it('delete 零命中 → canned「没找到符合条件的记录」，不开卡', async () => {
    aiPath({ intent: 'delete', reply: '', filter: { keyword: '游艇' } })
    mockBridge.listTransactionsByBook.mockResolvedValue([opTx({ id: 'a' })])
    const { result } = setupOp()

    await act(async () => { await result.current.submitInput('删掉游艇的消费') })

    expect(result.current.pendingOp).toBeNull()
    expect(lastMsg(result).text).toBe('没找到符合条件的记录')
    expect(mockBridge.softDeleteTransaction).not.toHaveBeenCalled()
  })

  it('无范围守卫：delete 无 filter / edit 无 set → 引导气泡，不查库不开卡', async () => {
    aiPath({ intent: 'delete', reply: '', filter: null })
    const { result } = setupOp()
    await act(async () => { await result.current.submitInput('删掉所有记录') })
    expect(result.current.pendingOp).toBeNull()
    expect(lastMsg(result).text).toContain('整账本删除')
    expect(mockBridge.listTransactionsByBook).not.toHaveBeenCalled()

    aiPath({ intent: 'edit', reply: '', filter: { keyword: '打车' }, set: null })
    const r2 = setupOp()
    await act(async () => { await r2.result.current.submitInput('改一下打车那笔') })
    expect(r2.result.current.pendingOp).toBeNull()
    expect(lastMsg(r2.result).text).toContain('想改哪些记录的什么内容呢')
  })

  it('部分失败可见 → 模板回执含失败数与中文化原因（复用 T12-8 表）', async () => {
    aiPath({ intent: 'edit', reply: '好', filter: { keyword: '打车' }, set: { amountCents: 100 } })
    mockBridge.listTransactionsByBook.mockResolvedValue([opTx({ id: 'a' }), opTx({ id: 'b' })])
    const { result } = setupOp()
    await act(async () => { await result.current.submitInput('把打车都改成1块') })
    mockBridge.updateTransaction
      .mockRejectedValueOnce(Object.assign(new Error('transaction version conflict'), {}))
      .mockResolvedValueOnce({ id: 'b', version: 8 })

    await act(async () => { await result.current.confirmOp() })

    expect(lastMsg(result).text)
      .toBe('已更新 1 笔，1 笔失败（该笔记录刚被更新，请关闭编辑层后重新打开重试）')
    expect(result.current.pendingOp).toBeNull()
  })

  it('全部失败 → ❌ 回执且不跑写后真同步', async () => {
    aiPath({ intent: 'delete', reply: '', filter: { keyword: '打车' } })
    mockBridge.listTransactionsByBook.mockResolvedValue([opTx({ id: 'a' })])
    const { result } = setupOp()
    await act(async () => { await result.current.submitInput('删掉带打字的') })
    mockBridge.softDeleteTransaction.mockRejectedValueOnce(new Error('db down'))

    await act(async () => { await result.current.confirmOp() })

    expect(lastMsg(result).text).toContain('❌ 删除失败')
    expect(mockBridge.triggerAuthSync).not.toHaveBeenCalled()
  })

  it('批量上限 50 截断：命中 55 → 执行 50 笔，回执含未处理数', async () => {
    aiPath({ intent: 'delete', reply: '', filter: { keyword: '打车' } })
    mockBridge.listTransactionsByBook.mockResolvedValue(
      Array.from({ length: 55 }, (_, i) => opTx({ id: `t${i}` })),
    )
    const { result } = setupOp()
    await act(async () => { await result.current.submitInput('删掉所有打车记录') })
    expect(result.current.pendingOp!.candidates).toHaveLength(55)

    await act(async () => { await result.current.confirmOp() })

    expect(mockBridge.softDeleteTransaction).toHaveBeenCalledTimes(50)
    expect(lastMsg(result).text).toBe('已删除 50 笔，超出单次 50 笔上限，5 笔未处理')
  })

  it('取消 → 不落任何写，回执「已取消」', async () => {
    aiPath({ intent: 'delete', reply: '', filter: { keyword: '打车' } })
    mockBridge.listTransactionsByBook.mockResolvedValue([opTx({ id: 'a' })])
    const { result } = setupOp()
    await act(async () => { await result.current.submitInput('删掉打车') })
    expect(result.current.pendingOp).not.toBeNull()

    act(() => { result.current.cancelOp() })

    expect(result.current.pendingOp).toBeNull()
    expect(mockBridge.softDeleteTransaction).not.toHaveBeenCalled()
    expect(mockBridge.updateTransaction).not.toHaveBeenCalled()
    expect(lastMsg(result).text).toBe('已取消')
  })

  it('新输入接管会话 → 未决 pendingOp 作废且不执行', async () => {
    aiPath({ intent: 'delete', reply: '', filter: { keyword: '打车' } })
    mockBridge.listTransactionsByBook.mockResolvedValue([opTx({ id: 'a' })])
    const { result } = setupOp()
    await act(async () => { await result.current.submitInput('删掉打车') })
    expect(result.current.pendingOp).not.toBeNull()

    aiPath({ intent: 'chat', reply: '在的' })
    await act(async () => { await result.current.submitInput('你好') })

    expect(result.current.pendingOp).toBeNull()
    expect(mockBridge.softDeleteTransaction).not.toHaveBeenCalled()
  })

  it('bookkeeping intent（及老形状无 intent）→ 现链路提案+写入，不碰预览卡', async () => {
    aiPath({
      intent: 'bookkeeping', type: 'expense', amountCents: 1250, category: '餐饮',
      note: '午饭', bizDate: '', confidence: 0.9,
    })
    const { result } = setupOp()

    await act(async () => { await result.current.submitInput('午饭十二块五') })

    await waitFor(() => expect(result.current.cardStatus).toBe('committed'))
    expect(result.current.pendingOp).toBeNull()
    expect(mockBridge.insertTransaction.mock.calls[0][0].categoryId).toBe('c-food')
    expect(mockBridge.updateTransaction).not.toHaveBeenCalled()

    // 老形状（无 intent 字段）同样走现链路
    aiPath({ type: 'expense', amountCents: 800, category: '交通', note: '公交', bizDate: '', confidence: 0.95 })
    const r2 = setupOp()
    await act(async () => { await r2.result.current.submitInput('公交八块') })
    await waitFor(() => expect(r2.result.current.cardStatus).toBe('committed'))
    expect(r2.result.current.pendingOp).toBeNull()
  })

  it('aiCategories/today → context 注入（REQ-37 ②）', async () => {
    aiPath(null) // interpretAI 返回空 output → 回退本地；本例只看调用参数
    const { result } = setupOp({ aiCategories: ['餐饮', '交通'] })

    await act(async () => { await result.current.submitInput('随便') })

    const [, ctx] = mockBridge.interpretAI.mock.calls[0]
    expect(ctx.categories).toEqual(['餐饮', '交通'])
    expect(ctx.today).toBe(todayBizDate(TZ))
  })

  it('★ T18-8：AI 失败 → 中断 + 重发（不再按「本地只解析出 chat」降级成 canned）', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal({
      action: 'chat', transactions: undefined, message: '抱歉，我没理解。', confidence: 0.1,
    }))
    mockBridge.isAvailable.mockReturnValue(true)
    mockBridge.interpretAI.mockRejectedValue(Object.assign(new Error('down'), { code: '503' }))
    const { result } = setupOp()

    await act(async () => { await result.current.submitInput('帮我删掉上周的') })

    expect(result.current.interrupted).toEqual({ input: '帮我删掉上周的' })
    expect(result.current.messages.some(m => m.text.includes(INTERRUPTED_TURN_NOTE))).toBe(true)
    expect(result.current.messages.some(m => m.text.includes('云端 AI 暂时不可用'))).toBe(true)
    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
  })
})

// T12-12 真机收口发现：本地 NLU 把操作句里的金额当新账解析（「把昨天的午饭改成15块」
// 实测被记成新的 ¥15）。
// T18-8 之后这道守卫（OP_SENTENCE_RE）**只服务离线**——在线一律走云端，不再需要它把
// 句子「救」到云端；离线时它保证操作句既不走本地写、也不误报。
describe('use-assistant — 操作句式守卫（T12-12 / T18-8 收窄为离线守卫）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.insertTransaction.mockResolvedValue({ id: 'tx-1', version: 1 })
    mockBridge.updateTransaction.mockResolvedValue({ id: 'tx-1', version: 8 })
    mockBridge.softDeleteTransaction.mockResolvedValue({ success: true })
    mockBridge.listTransactionsByBook.mockResolvedValue([])
    mockBridge.triggerAuthSync.mockResolvedValue({ outcome: 'OK' })
    mockBridge.isAvailable.mockReturnValue(false)
    // T14-4：messages 外置到模块级 store——逐用例复位防串
    useAssistantChatStore.setState({ messages: [] })
  })

  it('操作句 + 本地高置信误判为新账 → 云端 edit 分发接管，不写库', async () => {
    aiPath({ intent: 'edit', reply: '改这几笔', filter: { keyword: '打车' }, set: { amountCents: 500 } })
    // 模拟本地把「改成5块」误解析成高置信新账提案（真机复现的错路）
    vi.mocked(parseInput).mockReturnValue(txProposal({ confidence: 0.95 }))
    mockBridge.listTransactionsByBook.mockResolvedValue([opTx({ id: 'a' })])
    const { result } = setupOp()

    await act(async () => { await result.current.submitInput('把打车那笔改成5块') })

    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
    expect(result.current.pendingOp).toMatchObject({ kind: 'edit' })
    expect(result.current.pendingOp!.candidates.map(t => t.id)).toEqual(['a'])
  })

  it('★ T18-8：云端失败 + 操作句 → 中断 + 重发，不回落本地写账', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal({ confidence: 0.95 }))
    mockBridge.isAvailable.mockReturnValue(true)
    mockBridge.interpretAI.mockRejectedValue(Object.assign(new Error('down'), { code: '503' }))
    const { result } = setupOp()

    await act(async () => { await result.current.submitInput('把打车那笔改成5块') })

    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
    expect(result.current.interrupted).toEqual({ input: '把打车那笔改成5块' })
    expect(result.current.messages.some(m => m.text.includes(INTERRUPTED_TURN_NOTE))).toBe(true)
  })

  it('桥不可用 + 操作句 → canned 早退，不打云端不写库', async () => {
    vi.mocked(parseInput).mockReturnValue(txProposal({ confidence: 0.95 }))
    mockBridge.isAvailable.mockReturnValue(false)
    const { result } = setupOp()

    await act(async () => { await result.current.submitInput('删掉打车那笔') })

    expect(mockBridge.interpretAI).not.toHaveBeenCalled()
    expect(mockBridge.insertTransaction).not.toHaveBeenCalled()
    expect(lastMsg(result).text).toBe(AI_OFFLINE_CANNED)
  })
})

// ─── 第十五轮 T15-7（REQ-56 / 决策 #8）：edit 契约 v4 全字段端到端 ─────────────

const v4Accounts: AccountData[] = [
  { id: 'a-wx', name: '微信零钱', icon: '💬', version: 1 },
  { id: 'a-test', name: '测试', icon: '🧪', version: 1 },
]
const v4Books: BookData[] = [
  { id: 'book-1', ownerId: 'local', name: '日常', icon: '📒', version: 1 },
  { id: 'book-2', ownerId: 'local', name: '旅行', icon: '✈️', version: 1 },
  { id: 'book-m', ownerId: 'local', name: '总账本', icon: '📚', version: 1, isMaster: true },
]

function setupV4() {
  return renderHook(() => useAssistant({
    categories: cats, bookId: 'book-1', defaultAccountId: null, timezone: TZ,
    books: v4Books, accounts: v4Accounts,
  }))
}

describe('use-assistant — edit 契约 v4（T15-7 / REQ-56）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.insertTransaction.mockResolvedValue({ id: 'tx-1', version: 1 })
    mockBridge.updateTransaction.mockResolvedValue({ id: 'tx-1', version: 8 })
    mockBridge.softDeleteTransaction.mockResolvedValue({ success: true })
    mockBridge.listTransactionsByBook.mockResolvedValue([])
    mockBridge.triggerAuthSync.mockResolvedValue({ outcome: 'OK' })
    mockBridge.isAvailable.mockReturnValue(false)
    useAssistantChatStore.setState({ messages: [] })
  })

  it('改收支类型：set.type=income → 更新收 type 翻转（无同名分类保留原分类）', async () => {
    aiPath({
      intent: 'edit', reply: '好', filter: { keyword: '打牌' },
      set: { type: 'income', amountCents: 42000 },
    })
    mockBridge.listTransactionsByBook.mockResolvedValue([opTx({ id: 'a', note: '打牌' })])
    const { result } = setupV4()
    await act(async () => { await result.current.submitInput('打牌这笔改成收入420') })
    await act(async () => { await result.current.confirmOp() })
    const [input] = mockBridge.updateTransaction.mock.calls[0]
    expect(input).toMatchObject({ id: 'a', type: 'income', amountCents: 42000, categoryId: 'c-car', version: 8 })
  })

  it('改账户：set.accountName 命中 → accountId 回写新账户', async () => {
    aiPath({
      intent: 'edit', reply: '好', filter: { keyword: '打车' },
      set: { accountName: '测试', amountCents: 500 },
    })
    mockBridge.listTransactionsByBook.mockResolvedValue([opTx({ id: 'a', accountId: 'a-wx' })])
    const { result } = setupV4()
    await act(async () => { await result.current.submitInput('打车这笔账户改成测试') })
    await act(async () => { await result.current.confirmOp() })
    const [input] = mockBridge.updateTransaction.mock.calls[0]
    expect(input).toMatchObject({ id: 'a', accountId: 'a-test', amountCents: 500 })
  })

  it('改账本（挪记录）：filter.bookName 他账 → 跨账本取数，set.bookName 回写新 bookId', async () => {
    aiPath({
      intent: 'edit', reply: '好', filter: { bookName: '旅行', keyword: '打车' },
      set: { bookName: '日常' },
    })
    mockBridge.listTransactionsByBook.mockImplementation(async (bid: string) =>
      bid === 'book-2' ? [opTx({ id: 'r1', bookId: 'book-2' })] : [])
    const { result } = setupV4()
    await act(async () => { await result.current.submitInput('旅行账本里的打车那笔挪回日常') })
    expect(mockBridge.listTransactionsByBook).toHaveBeenCalledWith('book-2')
    expect(result.current.pendingOp!.candidates.map(t => t.id)).toEqual(['r1'])
    await act(async () => { await result.current.confirmOp() })
    const [input] = mockBridge.updateTransaction.mock.calls[0]
    expect(input).toMatchObject({ id: 'r1', bookId: 'book-1' })
  })

  it('filter.bookName 指向 master 总账本 → 引导不开卡不查库', async () => {
    aiPath({ intent: 'edit', reply: '好', filter: { bookName: '总账本' }, set: { amountCents: 500 } })
    const { result } = setupV4()
    await act(async () => { await result.current.submitInput('总账本那笔改成5块') })
    expect(mockBridge.listTransactionsByBook).not.toHaveBeenCalled()
    expect(result.current.pendingOp).toBeNull()
    expect(lastMsg(result).text).toContain('没找到叫「总账本」的账本')
  })

  it('filter.accountName 匹配不到 → 引导（列现有账户）不开卡不查库', async () => {
    aiPath({
      intent: 'edit', reply: '好', filter: { accountName: '支付宝', keyword: '打车' },
      set: { amountCents: 500 },
    })
    const { result } = setupV4()
    await act(async () => { await result.current.submitInput('支付宝里的打车改成5块') })
    expect(mockBridge.listTransactionsByBook).not.toHaveBeenCalled()
    expect(result.current.pendingOp).toBeNull()
    expect(lastMsg(result).text).toContain('没找到叫「支付宝」的账户')
    expect(lastMsg(result).text).toContain('微信零钱')
  })

  it('set.accountName 匹配不到但有其他字段 → 引导「该项未改」+ 剔除该键，执行不写账户', async () => {
    aiPath({
      intent: 'edit', reply: '好', filter: { keyword: '打车' },
      set: { accountName: '不存在的账户', amountCents: 500 },
    })
    mockBridge.listTransactionsByBook.mockResolvedValue([opTx({ id: 'a', accountId: 'a-wx' })])
    const { result } = setupV4()
    await act(async () => { await result.current.submitInput('打车那笔账户改不存在的账户金额改5块') })
    expect(lastMsg(result).text).toContain('没找到叫「不存在的账户」的账户')
    expect(lastMsg(result).text).toContain('该项未改')
    expect(result.current.pendingOp).not.toBeNull()
    expect(result.current.pendingOp!.set).toEqual({ amountCents: 500 })
    await act(async () => { await result.current.confirmOp() })
    const [input] = mockBridge.updateTransaction.mock.calls[0]
    expect(input).toMatchObject({ id: 'a', accountId: 'a-wx', amountCents: 500 })
  })

  it('set 仅点名对象且匹配不到 → 只引导不开卡不查库', async () => {
    aiPath({
      intent: 'edit', reply: '好', filter: { keyword: '打车' },
      set: { bookName: '不存在的账本' },
    })
    const { result } = setupV4()
    await act(async () => { await result.current.submitInput('打车那笔挪到不存在的账本') })
    expect(mockBridge.listTransactionsByBook).not.toHaveBeenCalled()
    expect(result.current.pendingOp).toBeNull()
    expect(lastMsg(result).text).toContain('没找到叫「不存在的账本」的账本')
  })

  it('delete 跨账本圈定：filter.bookName 命中 → 软删他账记录', async () => {
    aiPath({ intent: 'delete', reply: '', filter: { bookName: '旅行', keyword: '打车' } })
    mockBridge.listTransactionsByBook.mockImplementation(async (bid: string) =>
      bid === 'book-2' ? [opTx({ id: 'r1', bookId: 'book-2' })] : [])
    const { result } = setupV4()
    await act(async () => { await result.current.submitInput('删掉旅行账本的打车那笔') })
    expect(mockBridge.listTransactionsByBook).toHaveBeenCalledWith('book-2')
    expect(result.current.pendingOp!.candidates.map(t => t.id)).toEqual(['r1'])
    await act(async () => { await result.current.confirmOp() })
    expect(mockBridge.softDeleteTransaction).toHaveBeenCalledWith('r1', expect.any(String))
  })
})
