/**
 * HomePage 测试 — T10-8 收口补测（REQ-17 / 决策 #1）。
 *
 * 预算进度"已用 N%"只统计 includeInBudget !== false 的支出；
 * "本月总额"不过滤（含不计入项）；旧数据缺省字段=计入。
 *
 * 第十五轮 REQ-50（T15-1）追加：问候昵称拼接/空、存钱行渲染与无目标
 * 不渲染、空态引导句删除、预算匹配扩当年（annual）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, within, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { HomePage } from '../../features/ledger/HomePage'
import { todayBizDate } from '../../domain/date'
import type { TransactionData, SavingsGoalData } from '../../platform/bridge/bridge-types'
import { useSyncStore } from '../../platform/store/sync-store'

const mockBridge = vi.hoisted(() => ({
  call: vi.fn(),
  getMeta: vi.fn().mockResolvedValue({}),
  isAvailable: () => true,
  // T12-10：TxEditSheet 挂载时拉取（开层用例用）
  listBooks: vi.fn().mockResolvedValue([]),
  listAccounts: vi.fn().mockResolvedValue([]),
  // T15-1：HomePage 存钱目标加载
  listSavingsGoals: vi.fn().mockResolvedValue([]),
}))
vi.mock('../../platform/hooks/use-bridge', () => ({ useBridge: () => mockBridge }))
vi.mock('../../components/SyncBadge', () => ({
  SyncBadge: () => <span data-testid="sync-badge" />,
}))

const curMonth = todayBizDate('Asia/Shanghai').slice(0, 7)
const curYear = todayBizDate('Asia/Shanghai').slice(0, 4)

const mkTx = (over: Partial<TransactionData> & { id: string }): TransactionData => ({
  bookId: 'b1',
  type: 'expense',
  amountCents: 1000,
  categoryId: 'c1',
  accountId: null,
  note: '',
  occurredAt: Date.now(),
  source: 'manual',
  version: 1,
  deletedAt: null,
  bizDate: `${curMonth}-03`,
  ...over,
})

describe('HomePage 预算进度过滤（REQ-17 决策 #1）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.getMeta.mockResolvedValue({})
  })

  function setup(txs: TransactionData[], budgetCents: number) {
    mockBridge.call.mockImplementation((cmd: string) => {
      if (cmd === 'book.list') return Promise.resolve([{ id: 'b1', name: '默认账本', version: 1 }])
      if (cmd === 'category.list') return Promise.resolve([{ id: 'c1', kind: 'expense', name: '餐饮', parentId: null, version: 1 }])
      // T15-1 起匹配口径读 budgetType（BudgetPage 同口径），mock 补该字段
      if (cmd === 'budget.list') return Promise.resolve([{ amountCents: budgetCents, period: curMonth, budgetType: 'monthly' }])
      if (cmd === 'transaction.listByBook') return Promise.resolve(txs)
      return Promise.resolve([])
    })
  }

  it('已用% 只含计入项；本月总额含全部（不计入项也在）', async () => {
    setup(
      [
        mkTx({ id: 'a', amountCents: 10000, includeInBudget: true }),
        mkTx({ id: 'b', amountCents: 5000, includeInBudget: false }),
        mkTx({ id: 'c', amountCents: 300 }), // 缺省字段 = 计入
      ],
      100000,
    )
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )
    // 计入 = 10000+300 = 10300/100000 = 10.3% → round 10%
    await waitFor(() => expect(screen.getByText(/已用 10%/)).toBeInTheDocument())
    // 总额 = 15300 → ¥153.00（不过滤）
    expect(screen.getByText(/¥153\.00/)).toBeInTheDocument()
  })

  it('旧数据无 includeInBudget 字段按计入处理', async () => {
    setup([mkTx({ id: 'a', amountCents: 4000 })], 10000)
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText(/已用 40%/)).toBeInTheDocument())
  })

  it('今日流水行点击打开 TxEditSheet，金额预填自行数据（T12-10 / REQ-36）', async () => {
    const today = todayBizDate('Asia/Shanghai')
    setup([mkTx({ id: 't1', bizDate: today, amountCents: 5500 })], 100000)
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('餐饮')).toBeInTheDocument())
    fireEvent.click(screen.getByText('餐饮'))
    const sheet = await screen.findByTestId('tx-edit-sheet')
    expect(within(sheet).getByTestId('tx-edit-amount')).toHaveValue(55)
  })

  it('流水行=明细视觉：备注优先为 title（semibold 15px）、副行「时间 · 分类」、金额 17px（T14-1 / REQ-38）', async () => {
    const today = todayBizDate('Asia/Shanghai')
    setup([mkTx({ id: 't2', bizDate: today, note: '午饭加鸡腿', amountCents: 1234 })], 100000)
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )
    // 备注优先：title 显示备注（非分类名），且字号层级 semibold 15px
    const title = await screen.findByText('午饭加鸡腿')
    expect(title).toHaveClass('text-[15px]', 'font-semibold')
    // 分类名退到副行（时间 · 分类），备注不再单独占行
    const sub = screen.getByText(/· 餐饮$/)
    expect(sub).toHaveClass('text-xs')
    // 金额为 17px bold，支出去 ¥ 加负号
    const amount = screen.getByText('-12.34')
    expect(amount).toHaveClass('money-num', 'text-[17px]', 'font-bold')
  })
})

// ─── T15-1 / REQ-50：首页三合一（昵称问候 + 存钱行 + 预算当年口径 + 删引导句）───

/** 与 HomePage.getGreeting 同时钟口径（测试不断言具体时段，只断言拼接） */
function greetingNow(): string {
  const h = new Date().getHours()
  if (h >= 5 && h < 12) return '早上好'
  if (h >= 12 && h < 14) return '中午好'
  if (h >= 14 && h < 18) return '下午好'
  return '晚上好'
}

const mkGoal = (
  over: Partial<SavingsGoalData> & { id: string; name: string; targetCents: number; currentCents: number },
): SavingsGoalData => ({
  icon: null,
  periodValue: null,
  periodUnit: null,
  createdAt: 0,
  updatedAt: 0,
  version: 1,
  ...over,
})

describe('HomePage 三合一（T15-1 / REQ-50）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.getMeta.mockResolvedValue({})
    mockBridge.listSavingsGoals.mockResolvedValue([])
  })

  function setupT15(opts: {
    displayName?: string | null
    goals?: SavingsGoalData[]
    budgets?: { amountCents: number; period: string; budgetType: 'monthly' | 'annual' }[]
    txs?: TransactionData[]
  } = {}) {
    mockBridge.getMeta.mockResolvedValue({ display_name: opts.displayName ?? null })
    mockBridge.listSavingsGoals.mockResolvedValue(opts.goals ?? [])
    mockBridge.call.mockImplementation((cmd: string) => {
      if (cmd === 'book.list') return Promise.resolve([{ id: 'b1', name: '默认账本', version: 1 }])
      if (cmd === 'category.list') return Promise.resolve([{ id: 'c1', kind: 'expense', name: '餐饮', parentId: null, version: 1 }])
      if (cmd === 'budget.list') return Promise.resolve(opts.budgets ?? [])
      if (cmd === 'transaction.listByBook') return Promise.resolve(opts.txs ?? [])
      return Promise.resolve([])
    })
  }

  function renderHome() {
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )
  }

  it('昵称非空 → 问候行拼「，{nick}」', async () => {
    setupT15({ displayName: '小明' })
    renderHome()
    await waitFor(() => expect(screen.getByText(`${greetingNow()}，小明`)).toBeInTheDocument())
  })

  it('昵称为空 → 只显问候，不拼逗号', async () => {
    setupT15({ displayName: null })
    renderHome()
    await waitFor(() => expect(screen.getByText(greetingNow())).toBeInTheDocument())
    expect(screen.queryByText(/，/)).toBeNull()
  })

  it('存钱目标行：icon·名称 + ¥current/¥target + 进度条，最多 2 行', async () => {
    setupT15({
      goals: [
        mkGoal({ id: 'g1', name: '旅行基金', currentCents: 30000, targetCents: 150000, icon: '✈️' }),
        mkGoal({ id: 'g2', name: '换新手机', currentCents: 50000, targetCents: 100000 }),
        mkGoal({ id: 'g3', name: '第三目标', currentCents: 1, targetCents: 2 }),
      ],
    })
    renderHome()
    const rows = await waitFor(() => screen.getAllByTestId('home-savings-row'))
    expect(rows).toHaveLength(2)
    expect(screen.getByText('✈️ · 旅行基金')).toBeInTheDocument()
    // icon null → 🐷 兜底（与 SavingsPage 默认图标一致）
    expect(screen.getByText(/· 换新手机/)).toBeInTheDocument()
    expect(screen.getByText('¥300.00/¥1500.00')).toBeInTheDocument()
    // 第 3 条不渲染
    expect(screen.queryByText(/第三目标/)).toBeNull()
  })

  it('无存钱目标 → 不渲染存钱区（加载完成后）', async () => {
    const today = todayBizDate('Asia/Shanghai')
    setupT15({ goals: [], txs: [mkTx({ id: 't1', bizDate: today })] })
    renderHome()
    // 今日流水渲染完成 = loadData 已落地，此时存钱区仍为 0 才是真不渲染
    await waitFor(() => expect(screen.getByText('-10.00')).toBeInTheDocument())
    expect(screen.queryByTestId('home-savings-row')).toBeNull()
  })

  it('空态保留「今天还没有记录」，引导句已删（REQ-50 ③）', async () => {
    setupT15()
    renderHome()
    await waitFor(() => expect(screen.getByText('今天还没有记录')).toBeInTheDocument())
    expect(screen.queryByText(/记第一笔/)).toBeNull()
    expect(screen.queryByText(/问问小贝/)).toBeNull()
  })

  it('仅年度预算 → 「本年…年度预算」，进度按当年计入支出（REQ-50 ②口径扩展）', async () => {
    setupT15({
      budgets: [{ amountCents: 120000, period: curYear, budgetType: 'annual' }],
      txs: [
        mkTx({ id: 'a', amountCents: 12000, includeInBudget: true }),
        mkTx({ id: 'b', bizDate: `${curMonth}-04`, amountCents: 1000, includeInBudget: false }),
      ],
    })
    renderHome()
    // 计入 = 12000/120000 = 10%；总额 13000 → ¥130.00（不过滤，同决策 #1）
    await waitFor(() => expect(screen.getByText(/本年 ¥130\.00 · 已用 10%/)).toBeInTheDocument())
    expect(screen.getByText(/年度预算 ¥1200\.00/)).toBeInTheDocument()
  })

  it('当月与当年预算同时命中 → 当月优先', async () => {
    setupT15({
      budgets: [
        { amountCents: 120000, period: curYear, budgetType: 'annual' },
        { amountCents: 100000, period: curMonth, budgetType: 'monthly' },
      ],
      txs: [mkTx({ id: 'a', amountCents: 20000 })],
    })
    renderHome()
    await waitFor(() => expect(screen.getByText(/已用 20%/)).toBeInTheDocument())
    expect(screen.getByText(/预算 ¥1000\.00/)).toBeInTheDocument()
    expect(screen.queryByText(/年度预算/)).toBeNull()
  })
})

// ─── T18-2 / REQ-67①：写后刷新 —— 同步完成 / 页面重新可见时重取 ───

describe('HomePage 同步重取（T18-2 / REQ-67①）', () => {
  const today = todayBizDate('Asia/Shanghai')

  /** 模拟一轮同步完成（原生推来的终态事件 → store） */
  const emitSynced = (at: number) => {
    act(() => {
      useSyncStore.setState(s => ({ status: { ...s.status, state: 'idle', lastSyncedAt: at } }))
    })
  }

  beforeEach(() => {
    vi.clearAllMocks()
    useSyncStore.setState(s => ({
      status: { ...s.status, state: 'idle', pendingCount: 0, lastSyncedAt: 0, failedMutations: [] },
    }))
  })

  it('同步轮完成（lastSyncedAt 前进）→ 重取，页面显示新值（修复前只有重挂/重启才更新）', async () => {
    // 页面挂载前已经同步过（lastSyncedAt 非零）：基准 = 挂载那一刻的值，之后前进即重取
    useSyncStore.setState(s => ({ status: { ...s.status, lastSyncedAt: 1000 } }))
    // 本地库（Room）在第一次读时还是旧值；同步把服务端权威行拉回来后变成新值
    let amountCents = 500
    mockBridge.call.mockImplementation((cmd: string) => {
      if (cmd === 'book.list') return Promise.resolve([{ id: 'b1', name: '默认账本', version: 1 }])
      if (cmd === 'category.list') return Promise.resolve([{ id: 'c1', kind: 'expense', name: '餐饮', parentId: null, version: 1 }])
      if (cmd === 'budget.list') return Promise.resolve([])
      if (cmd === 'transaction.listByBook') return Promise.resolve([mkTx({ id: 'a', amountCents, bizDate: today })])
      return Promise.resolve([])
    })
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('-5.00')).toBeInTheDocument())

    amountCents = 600
    emitSynced(2000) // 前进 → 重取
    await waitFor(() => expect(screen.getByText('-6.00')).toBeInTheDocument())
  })

  it('页面重新可见（visibilitychange → visible）→ 重取', async () => {
    let amountCents = 500
    mockBridge.call.mockImplementation((cmd: string) => {
      if (cmd === 'book.list') return Promise.resolve([{ id: 'b1', name: '默认账本', version: 1 }])
      if (cmd === 'category.list') return Promise.resolve([{ id: 'c1', kind: 'expense', name: '餐饮', parentId: null, version: 1 }])
      if (cmd === 'budget.list') return Promise.resolve([])
      if (cmd === 'transaction.listByBook') return Promise.resolve([mkTx({ id: 'a', amountCents, bizDate: today })])
      return Promise.resolve([])
    })
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('-5.00')).toBeInTheDocument())

    amountCents = 700
    act(() => { document.dispatchEvent(new Event('visibilitychange')) })
    await waitFor(() => expect(screen.getByText('-7.00')).toBeInTheDocument())
  })

  it('挂载时还没有过同步（lastSyncedAt=0）→ 首轮同步完成也要重取（冷启动就不该停在旧值）', async () => {
    // 冷启动：应用刚起来、首轮同步尚未完成，用户已经切到首页——此时 store 里
    // lastSyncedAt 还是 0。若把「0 → 非零」当作基准而非变化，首轮同步把权威行
    // 拉回来时页面不会重取，用户要再切一次页才看得见新数据。
    let amountCents = 500
    mockBridge.call.mockImplementation((cmd: string) => {
      if (cmd === 'book.list') return Promise.resolve([{ id: 'b1', name: '默认账本', version: 1 }])
      if (cmd === 'category.list') return Promise.resolve([{ id: 'c1', kind: 'expense', name: '餐饮', parentId: null, version: 1 }])
      if (cmd === 'budget.list') return Promise.resolve([])
      if (cmd === 'transaction.listByBook') return Promise.resolve([mkTx({ id: 'a', amountCents, bizDate: today })])
      return Promise.resolve([])
    })
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('-5.00')).toBeInTheDocument())

    amountCents = 900
    emitSynced(1500) // 首轮同步完成：0 → 1500
    await waitFor(() => expect(screen.getByText('-9.00')).toBeInTheDocument())
  })
})

// ─── T21-8 / REQ-83：待确认箱入口（仅当 pendingConfirmCount > 0 时出现）───

describe('HomePage 待确认箱入口（T21-8 / REQ-83）', () => {
  const today = todayBizDate('Asia/Shanghai')

  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.getMeta.mockResolvedValue({})
    mockBridge.listSavingsGoals.mockResolvedValue([])
  })

  function setupWithAutobook(pendingConfirmCount: number) {
    mockBridge.call.mockImplementation((cmd: string) => {
      if (cmd === 'book.list') return Promise.resolve([{ id: 'b1', name: '默认账本', version: 1 }])
      if (cmd === 'category.list') return Promise.resolve([{ id: 'c1', kind: 'expense', name: '餐饮', parentId: null, version: 1 }])
      if (cmd === 'budget.list') return Promise.resolve([])
      if (cmd === 'transaction.listByBook') return Promise.resolve([mkTx({ id: 't1', bizDate: today })])
      if (cmd === 'autobook.get_status') {
        return Promise.resolve({
          enabled: true,
          accessibilityGranted: true,
          notificationGranted: true,
          todayCaptureCount: 2,
          pendingConfirmCount,
        })
      }
      return Promise.resolve([])
    })
  }

  it('pendingConfirmCount > 0 → 显示待确认入口（带计数 + 文案）', async () => {
    setupWithAutobook(3)
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )
    const entry = await screen.findByTestId('home-autobook-entry')
    expect(entry).toBeInTheDocument()
    expect(entry).toHaveTextContent(/小贝发现了 3 笔待确认/)
    expect(entry).toHaveTextContent(/点进去确认或修改/)
  })

  it('pendingConfirmCount = 0 → 不显示待确认入口', async () => {
    setupWithAutobook(0)
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )
    // 等今日流水渲染完成 = loadData 已落地
    await waitFor(() => expect(screen.getByText('-10.00')).toBeInTheDocument())
    expect(screen.queryByTestId('home-autobook-entry')).toBeNull()
  })

  it('autobook.get_status 失败 → 入口不显示（降级到 0，不挡主流程）', async () => {
    mockBridge.call.mockImplementation((cmd: string) => {
      if (cmd === 'book.list') return Promise.resolve([{ id: 'b1', name: '默认账本', version: 1 }])
      if (cmd === 'category.list') return Promise.resolve([])
      if (cmd === 'budget.list') return Promise.resolve([])
      if (cmd === 'transaction.listByBook') return Promise.resolve([mkTx({ id: 't1', bizDate: today })])
      if (cmd === 'autobook.get_status') return Promise.reject(new Error('unavailable'))
      return Promise.resolve([])
    })
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('-10.00')).toBeInTheDocument())
    // 不因为 autobook 失败而崩、不显示入口
    expect(screen.queryByTestId('home-autobook-entry')).toBeNull()
  })
})
