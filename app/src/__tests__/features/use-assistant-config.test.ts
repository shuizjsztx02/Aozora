/**
 * 第十三轮 T13-5（REQ-48 / 决策 #6 #7 #8）：use-assistant config 分发矩阵测试。
 *
 * 覆盖：
 * - intent=config 好形状 → 名→id 解析成功开 pendingConfig（确认前零写入）
 * - 坏形状（config 缺失/op 坏）→ 回落 chat 转述 reply
 * - 解析失败（匹配不到/create 重名/期限过期）→ 引导语气泡，不开卡
 * - confirmConfig 逐 op 执行：insertBook / insertAccount / insertBudget /
 *   insertSavingsGoal / setMeta(default_*)，参数照现有页面创建流口径；回执消息；
 *   桥失败 → ❌ 回执（本地化）
 * - cancelConfig 不落写；新输入作废未决卡
 * - context.books/accounts/budgets/goals 名单段注入（决策 #7）
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useAssistant } from '../../features/assistant/use-assistant'
import { parseInput } from '../../domain/nlu/parser'
import type { ActionProposal } from '../../domain/action-proposal'
import type {
  AccountData, BookData, BudgetData, CategoryData, SavingsGoalData,
} from '../../platform/bridge/bridge-types'
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
  // T13-5：config 执行的五个桥命令
  insertBook: vi.fn().mockResolvedValue({ id: 'new-book' }),
  insertAccount: vi.fn().mockResolvedValue({ id: 'new-account' }),
  insertBudget: vi.fn().mockResolvedValue({ id: 'new-budget' }),
  insertSavingsGoal: vi.fn().mockResolvedValue({ id: 'new-goal' }),
  setMeta: vi.fn().mockResolvedValue({ success: true }),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

vi.mock('../../domain/nlu/parser', () => ({
  parseInput: vi.fn(),
}))

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const TZ = 'Asia/Shanghai'

const cats: CategoryData[] = [
  { id: 'c-food', kind: 'expense', name: '餐饮', parentId: null, version: 1 },
  { id: 'c-car', kind: 'expense', name: '交通', parentId: null, version: 1 },
]
const books: BookData[] = [
  { id: 'master-1', ownerId: 'o', name: '总账本', icon: '📒', version: 1, isMaster: true },
  { id: 'book-x', ownerId: 'o', name: '我的账本', icon: '📒', version: 1 },
]
const accounts: AccountData[] = [
  { id: 'acc-x', name: '现金', icon: '💵', version: 1 },
]
const budgets: BudgetData[] = [
  { id: 'bud-1', bookId: 'book-x', budgetType: 'monthly', categoryId: 'c-food', period: '2026-09', amountCents: 100000, version: 2 },
]
const goals: SavingsGoalData[] = [
  {
    id: 'goal-1', name: '新手机', targetCents: 800000, currentCents: 0, icon: '📱',
    periodValue: null, periodUnit: null, createdAt: 1, updatedAt: 1, version: 1,
  },
]

function chatProposal(): ActionProposal {
  return {
    proposalId: 'prop-chat', action: 'chat', confidence: 0.9, requiresConfirmation: false,
    message: '本地兜底', warnings: [],
  }
}

function setupConfig() {
  return renderHook(() => useAssistant({
    categories: cats, bookId: 'book-x', defaultAccountId: null, timezone: TZ,
    books, accounts, budgets, goals,
  }))
}

/** 走 AI 路径 + interpret 固定输出。 */
function aiPath(output: Record<string, unknown>) {
  vi.mocked(parseInput).mockReturnValue(chatProposal()) // action=chat → needsAI
  mockBridge.isAvailable.mockReturnValue(true)
  mockBridge.interpretAI.mockResolvedValue({ output })
}

function configOutput(config: unknown, reply = '好的'): Record<string, unknown> {
  return {
    intent: 'config', reply,
    type: '', amountCents: 0, category: '', note: '', bizDate: '', confidence: 0.9,
    config,
  }
}

const lastMsg = (result: { current: { messages: { text: string }[] } }) =>
  result.current.messages[result.current.messages.length - 1].text

beforeEach(() => {
  vi.clearAllMocks()
  mockBridge.isAvailable.mockReturnValue(false)
  mockBridge.validateProposal.mockResolvedValue({ valid: true })
  mockBridge.triggerAuthSync.mockResolvedValue({ outcome: 'OK' })
  mockBridge.insertBook.mockResolvedValue({ id: 'new-book' })
  mockBridge.insertAccount.mockResolvedValue({ id: 'new-account' })
  mockBridge.insertBudget.mockResolvedValue({ id: 'new-budget' })
  mockBridge.insertSavingsGoal.mockResolvedValue({ id: 'new-goal' })
  mockBridge.setMeta.mockResolvedValue({ success: true })
})

describe('submitInput → config 分发（T13-5）', () => {
  it('好形状：pendingConfig 开卡，确认前零写入', async () => {
    aiPath(configOutput({ op: 'book.create', name: '旅行' }))
    const { result } = setupConfig()
    await act(async () => { await result.current.submitInput('建一个叫旅行的账本') })

    expect(result.current.pendingConfig).not.toBeNull()
    expect(result.current.pendingConfig!.cfg.op).toBe('book.create')
    expect(result.current.pendingConfig!.preview.title).toBe('⚙️ 新建账本')
    expect(result.current.pendingConfig!.preview.rows).toContainEqual({ label: '名称', value: '旅行' })
    // 确认前不写
    expect(mockBridge.insertBook).not.toHaveBeenCalled()
    expect(mockBridge.setMeta).not.toHaveBeenCalled()
  })

  it('坏形状（config 缺失 / op 不在白名单）→ 回落 chat 转述 reply，不开卡不写', async () => {
    aiPath(configOutput(undefined, '你想新建什么呀？'))
    const { result } = setupConfig()
    await act(async () => { await result.current.submitInput('帮我搞个账本吧') })
    expect(result.current.pendingConfig).toBeNull()
    expect(lastMsg(result)).toBe('你想新建什么呀？')
    expect(mockBridge.insertBook).not.toHaveBeenCalled()

    aiPath(configOutput({ op: 'book.delete', name: '我的账本' }))
    await act(async () => { await result.current.submitInput('删掉我的账本') })
    expect(result.current.pendingConfig).toBeNull()
    expect(mockBridge.insertBook).not.toHaveBeenCalled()
  })

  it('reply 也空的坏形状 → 通用兜底语（answerTextIntent 口径）', async () => {
    aiPath(configOutput({ op: 'nope' }, ''))
    const { result } = setupConfig()
    await act(async () => { await result.current.submitInput('嗯') })
    expect(lastMsg(result)).toContain('答不上来')
  })

  it('default.book_set 匹配不到 → 引导语气泡（带现有名单），不开卡', async () => {
    aiPath(configOutput({ op: 'default.book_set', name: '健身' }))
    const { result } = setupConfig()
    await act(async () => { await result.current.submitInput('把健身设为默认账本') })
    expect(result.current.pendingConfig).toBeNull()
    expect(lastMsg(result)).toContain('没找到')
    expect(lastMsg(result)).toContain('我的账本')
    expect(mockBridge.setMeta).not.toHaveBeenCalled()
  })

  it('create 同类重名 → 引导语不开卡（旅行无→用现有名「我的账本」撞名）', async () => {
    aiPath(configOutput({ op: 'book.create', name: '我的账本' }))
    const { result } = setupConfig()
    await act(async () => { await result.current.submitInput('新建我的账本') })
    expect(result.current.pendingConfig).toBeNull()
    expect(lastMsg(result)).toContain('已经存在')
  })

  it('新输入接管会话：未决 config 卡作废（不静默执行）', async () => {
    aiPath(configOutput({ op: 'book.create', name: '旅行' }))
    const { result } = setupConfig()
    await act(async () => { await result.current.submitInput('建一个叫旅行的账本') })
    expect(result.current.pendingConfig).not.toBeNull()

    aiPath(configOutput({ op: 'book.create', name: '运动' }))
    await act(async () => { await result.current.submitInput('再建个运动账本') })
    // 新卡开出、旧卡被替换；仍未发生任何写入
    expect(result.current.pendingConfig!.cfg.name).toBe('运动')
    expect(mockBridge.insertBook).not.toHaveBeenCalled()
  })

  it('离线（桥不可用）：config 句本地 NLU 兜底 → canned，不炸', async () => {
    vi.mocked(parseInput).mockReturnValue(chatProposal())
    mockBridge.isAvailable.mockReturnValue(false)
    const { result } = setupConfig()
    await act(async () => { await result.current.submitInput('建一个叫旅行的账本') })
    expect(result.current.pendingConfig).toBeNull()
    expect(lastMsg(result)).toContain('断网')
  })
})

describe('confirmConfig 逐 op 执行（T13-5 卡片3）', () => {
  async function openAndConfirm(config: unknown) {
    aiPath(configOutput(config))
    const { result } = setupConfig()
    await act(async () => { await result.current.submitInput('帮我设置一下') })
    expect(result.current.pendingConfig).not.toBeNull()
    await act(async () => { await result.current.confirmConfig() })
    return result
  }

  it('book.create → insertBook（UUID id/mutationId、ownerId=local、icon 兜底 📒、version 1）+ 回执 + 写后真同步', async () => {
    const result = await openAndConfirm({ op: 'book.create', name: '旅行' })
    expect(mockBridge.insertBook).toHaveBeenCalledTimes(1)
    const [book, mutationId] = mockBridge.insertBook.mock.calls[0]
    expect(book).toEqual({ id: expect.any(String), ownerId: 'local', name: '旅行', icon: '📒', version: 1 })
    expect(book.id).toMatch(UUID_RE)
    expect(mutationId).toMatch(UUID_RE)
    expect(lastMsg(result)).toBe('已创建账本「旅行」')
    expect(result.current.pendingConfig).toBeNull()
    expect(mockBridge.triggerAuthSync).toHaveBeenCalled()
  })

  it('book.create icon 透传', async () => {
    await openAndConfirm({ op: 'book.create', name: '旅行', icon: '✈️' })
    expect(mockBridge.insertBook.mock.calls[0][0].icon).toBe('✈️')
  })

  it('account.create → 分类原样写入 + 余额真落库（REQ-69：不再折标签/引导补记）', async () => {
    const result = await openAndConfirm({ op: 'account.create', name: '工资卡', accountType: '储蓄卡', initBalance: 5000 })
    const [acc] = mockBridge.insertAccount.mock.calls[0]
    expect(acc).toEqual({
      id: expect.any(String), ownerId: 'local', name: '工资卡', icon: '💰',
      accountType: '储蓄卡', balanceCents: 500000, version: 1,
    })
    expect(acc.id).toMatch(UUID_RE)
    expect(lastMsg(result)).toBe('已创建账户「工资卡」（储蓄卡）；当前余额 ¥5000.00')
  })

  it('account.create 省略分类与余额：accountType=null、余额 0（不再要求 income|expense）', async () => {
    const result = await openAndConfirm({ op: 'account.create', name: '零钱包' })
    const [acc] = mockBridge.insertAccount.mock.calls[0]
    expect(acc).toMatchObject({ accountType: null, balanceCents: 0 })
    expect(lastMsg(result)).toBe('已创建账户「零钱包」')
  })

  it('account.create 信用卡欠款：负余额按「更新后的绝对值」落库', async () => {
    const result = await openAndConfirm({ op: 'account.create', name: '中信信用卡', accountType: '信用卡', initBalance: -2000 })
    const [acc] = mockBridge.insertAccount.mock.calls[0]
    expect(acc).toMatchObject({ accountType: '信用卡', balanceCents: -200000 })
    expect(lastMsg(result)).toBe('已创建账户「中信信用卡」（信用卡）；当前余额 -¥2000.00')
  })

  it('budget.create → insertBudget（categoryId 解析、period=当期、元→分）', async () => {
    const result = await openAndConfirm({ op: 'budget.create', name: 'B', category: '餐饮', amount: 2000 })
    const [bud] = mockBridge.insertBudget.mock.calls[0]
    const today = todayBizDate(TZ)
    expect(bud).toEqual({
      id: expect.any(String), bookId: 'book-x', budgetType: 'monthly',
      categoryId: 'c-food', period: today.slice(0, 7), amountCents: 200000, version: 1,
    })
    expect(bud.id).toMatch(UUID_RE)
    expect(lastMsg(result)).toBe('已为「餐饮」创建月度预算 ¥2000.00')
  })

  it('budget.create period=year → annual + 当年 period', async () => {
    await openAndConfirm({ op: 'budget.create', name: 'B', category: '交通', amount: 999.5, period: 'year' })
    const [bud] = mockBridge.insertBudget.mock.calls[0]
    expect(bud.budgetType).toBe('annual')
    expect(bud.period).toBe(todayBizDate(TZ).slice(0, 4))
    expect(bud.amountCents).toBe(99950)
  })

  it('savings.create → insertSavingsGoal（元→分、无期限 null 对、icon 🐷）', async () => {
    const result = await openAndConfirm({ op: 'savings.create', name: '买车', targetAmount: 100000 })
    const [goal] = mockBridge.insertSavingsGoal.mock.calls[0]
    expect(goal).toEqual({
      id: expect.any(String), ownerId: 'local', name: '买车',
      targetCents: 10000000, currentCents: 0, icon: '🐷',
      periodValue: null, periodUnit: null, version: 1,
    })
    expect(lastMsg(result)).toBe('已创建存钱目标「买车」（目标 ¥100000.00）')
  })

  it('savings.create deadline → 周/月期限换算随桥下发', async () => {
    const in30Days = new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 10)
    await openAndConfirm({ op: 'savings.create', name: '旅行基金', targetAmount: 8000, deadline: in30Days })
    const [goal] = mockBridge.insertSavingsGoal.mock.calls[0]
    expect(goal.periodUnit).toBe('week')
    expect(goal.periodValue).toBeGreaterThanOrEqual(4)
    expect(goal.periodValue).toBeLessThanOrEqual(5)
  })

  it('default.book_set → setMeta(default_book_id, 解析 id)', async () => {
    const result = await openAndConfirm({ op: 'default.book_set', name: '我的账本' })
    expect(mockBridge.setMeta).toHaveBeenCalledWith('default_book_id', 'book-x')
    expect(mockBridge.insertBook).not.toHaveBeenCalled()
    expect(lastMsg(result)).toBe('已把「我的账本」设为默认账本')
  })

  it('default.account_set → setMeta(default_account_id, 解析 id)', async () => {
    await openAndConfirm({ op: 'default.account_set', name: '现金' })
    expect(mockBridge.setMeta).toHaveBeenCalledWith('default_account_id', 'acc-x')
  })

  it('桥失败 → ❌ 回执（Kotlin 英文错误本地化），卡收口', async () => {
    mockBridge.insertBook.mockRejectedValueOnce(new Error('book ids are required'))
    aiPath(configOutput({ op: 'book.create', name: '旅行' }))
    const { result } = setupConfig()
    await act(async () => { await result.current.submitInput('建账本') })
    await act(async () => { await result.current.confirmConfig() })
    expect(result.current.pendingConfig).toBeNull()
    expect(lastMsg(result)).toContain('❌ 设置执行失败')
    expect(mockBridge.triggerAuthSync).not.toHaveBeenCalled()
  })

  it('重入互斥：执行在途二次 confirm 不双写', async () => {
    let resolveInsert: (v: { id: string }) => void = () => {}
    mockBridge.insertBook.mockImplementationOnce(
      () => new Promise(r => { resolveInsert = r }),
    )
    aiPath(configOutput({ op: 'book.create', name: '旅行' }))
    const { result } = setupConfig()
    await act(async () => { await result.current.submitInput('建账本') })
    let done = false
    act(() => { void result.current.confirmConfig().then(() => { done = true }) })
    act(() => { void result.current.confirmConfig() })
    expect(mockBridge.insertBook).toHaveBeenCalledTimes(1)
    resolveInsert({ id: 'new-book' })
    await waitFor(() => expect(done).toBe(true))
    expect(mockBridge.insertBook).toHaveBeenCalledTimes(1)
  })

  // 活体发现（2026-09-10 模拟器）：名单快照只在挂载装载 → 建账本成功后下一轮
  // 点名被拒（"把默认账本设成旅行" 回引导语）。修复=写入成功回调重拉名单。
  it('onConfigApplied：写入成功后调用（重拉名单）；确认前不调用', async () => {
    const onConfigApplied = vi.fn()
    aiPath(configOutput({ op: 'book.create', name: '旅行' }))
    const { result } = renderHook(() => useAssistant({
      categories: cats, bookId: 'book-x', defaultAccountId: null, timezone: TZ,
      books, accounts, budgets, goals, onConfigApplied,
    }))
    await act(async () => { await result.current.submitInput('建一个叫旅行的账本') })
    expect(onConfigApplied).not.toHaveBeenCalled() // 开卡≠应用
    await act(async () => { await result.current.confirmConfig() })
    expect(onConfigApplied).toHaveBeenCalledTimes(1)
  })

  it('onConfigApplied：桥失败与取消均不触发（无写入无需重拉）', async () => {
    const onConfigApplied = vi.fn()
    mockBridge.insertBook.mockRejectedValueOnce(new Error('boom'))
    aiPath(configOutput({ op: 'book.create', name: '旅行' }))
    const { result } = renderHook(() => useAssistant({
      categories: cats, bookId: 'book-x', defaultAccountId: null, timezone: TZ,
      books, accounts, budgets, goals, onConfigApplied,
    }))
    await act(async () => { await result.current.submitInput('建账本') })
    await act(async () => { await result.current.confirmConfig() })
    expect(onConfigApplied).not.toHaveBeenCalled()

    aiPath(configOutput({ op: 'book.create', name: '运动' }))
    await act(async () => { await result.current.submitInput('再建个运动账本') })
    act(() => { result.current.cancelConfig() })
    expect(onConfigApplied).not.toHaveBeenCalled()
  })
})

describe('cancelConfig（T13-5 防呆）', () => {
  it('取消 → 零写入 + 「已取消」回执 + 卡清空', async () => {
    aiPath(configOutput({ op: 'book.create', name: '旅行' }))
    const { result } = setupConfig()
    await act(async () => { await result.current.submitInput('建一个叫旅行的账本') })
    expect(result.current.pendingConfig).not.toBeNull()

    act(() => { result.current.cancelConfig() })
    expect(result.current.pendingConfig).toBeNull()
    expect(lastMsg(result)).toBe('已取消')
    expect(mockBridge.insertBook).not.toHaveBeenCalled()
    expect(mockBridge.setMeta).not.toHaveBeenCalled()
    expect(mockBridge.triggerAuthSync).not.toHaveBeenCalled()
  })
})

describe('context 对象名单段注入（决策 #7）', () => {
  it('books/accounts/budgets/goals 全量传入 → interpretAI context 四段 [{name}]', async () => {
    aiPath(configOutput({ op: 'book.create', name: '旅行' }))
    const { result } = setupConfig()
    await act(async () => { await result.current.submitInput('建一个叫旅行的账本') })

    expect(mockBridge.interpretAI).toHaveBeenCalledTimes(1)
    const ctx = mockBridge.interpretAI.mock.calls[0][1]
    expect(ctx.books).toEqual([{ name: '总账本' }, { name: '我的账本' }])
    expect(ctx.accounts).toEqual([{ name: '现金' }])
    // budgets 段以分类名作名单（实体无名列；总预算兜底）
    expect(ctx.budgets).toEqual([{ name: '餐饮' }])
    expect(ctx.goals).toEqual([{ name: '新手机' }])
  })

  it('空列表 → 四段传 null（服务端渲染段跳过）', async () => {
    aiPath(configOutput({ op: 'book.create', name: '旅行' }))
    const { result } = renderHook(() => useAssistant({
      categories: [], bookId: null, defaultAccountId: null, timezone: TZ,
    }))
    await act(async () => { await result.current.submitInput('建账本') })
    const ctx = mockBridge.interpretAI.mock.calls[0][1]
    expect(ctx).toMatchObject({ books: null, accounts: null, budgets: null, goals: null })
  })
})
