/**
 * DetailPage 组件测试（第六轮 REQ-1 回归防护）。
 *
 * 覆盖：
 * - 默认只展示当月流水 + 期间行右侧支出合计
 * - 搜索圆钮（T12-5/REQ-33）→ navigate('/search') 带当前账本；原内联搜索用例迁至 SearchPage.test
 * - 点击期间标签打开年/月面板，切到上一个月
 * - 年份 ‹ 切换后选中往年同月 → 空态
 * - 删除走 softDeleteTransaction 且 mutationId 为 UUID（Bug-5.2 契约）
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'

const mockNavigate = vi.hoisted(() => vi.fn())
vi.mock('react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

import { DetailPage } from '../../features/ledger/DetailPage'
import type { TransactionData, CategoryData } from '../../platform/bridge/bridge-types'
import { useSyncStore } from '../../platform/store/sync-store'

const mockBridge = vi.hoisted(() => ({
  // T9-5b（REQ-11）：meta.get/set mock（默认无默认选择）
  getMeta: vi.fn().mockResolvedValue({}),
  setMeta: vi.fn().mockResolvedValue({ success: true }),
  call: vi.fn(),
  softDeleteTransaction: vi.fn(),
  // T12-10：TxEditSheet 挂载时拉取（开层用例用）
  listBooks: vi.fn().mockResolvedValue([]),
  listAccounts: vi.fn().mockResolvedValue([]),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

// 相对"今天"构造上一个月 / 当月期间，避免用例随日期腐化
const NOW = new Date()
const curPeriod = `${NOW.getFullYear()}-${String(NOW.getMonth() + 1).padStart(2, '0')}`
const prevDate = new Date(NOW.getFullYear(), NOW.getMonth() - 1, 15)
const prevPeriod = `${prevDate.getFullYear()}-${String(prevDate.getMonth() + 1).padStart(2, '0')}`
const curLabel = `${NOW.getFullYear()}年${NOW.getMonth() + 1}月`
const prevLabel = `${prevDate.getFullYear()}年${prevDate.getMonth() + 1}月`

const CATS: CategoryData[] = [
  { id: 'cat-food', kind: 'expense', name: '餐饮', parentId: null, version: 1 },
  { id: 'cat-salary', kind: 'income', name: '工资', parentId: null, version: 1 },
]

const mkTx = (over: Partial<TransactionData> & { id: string; bizDate: string }): TransactionData => ({
  bookId: 'b1',
  type: 'expense',
  amountCents: 5500,
  categoryId: 'cat-food',
  accountId: null,
  note: '',
  occurredAt: new Date(`${over.bizDate}T12:00:00`).getTime(),
  source: 'manual',
  version: 1,
  deletedAt: null,
  ...over,
})

const TXS: TransactionData[] = [
  mkTx({ id: 'tx-cur', bizDate: `${curPeriod}-03`, note: '午饭' }),
  mkTx({ id: 'tx-prev-income', bizDate: `${prevPeriod}-05`, type: 'income', amountCents: 800000, categoryId: 'cat-salary', note: '八月工资' }),
]

const renderPage = () =>
  render(
    <MemoryRouter>
      <DetailPage />
    </MemoryRouter>
  )

describe('DetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.softDeleteTransaction.mockResolvedValue({ success: true })
    mockBridge.call.mockImplementation((cmd: string) => {
      if (cmd === 'book.list') return Promise.resolve([{ id: 'b1' }])
      if (cmd === 'transaction.listByBook') return Promise.resolve(TXS)
      if (cmd === 'category.list') return Promise.resolve(CATS)
      return Promise.resolve([])
    })
  })

  it('shows only current-month rows with expense total on the right', async () => {
    renderPage()
    expect(await screen.findByText(curLabel)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('午饭')).toBeInTheDocument())
    // 上个月的工资行不可见
    expect(screen.queryByText('八月工资')).not.toBeInTheDocument()
    // 当月支出合计 55.00 → 整元省略小数
    expect(screen.getByText('55')).toBeInTheDocument()
  })

  it('内联搜索条已迁出；圆钮跳转 /search 并携带当前账本 id（T12-5 / REQ-33）', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('午饭')
    // 原全宽搜索框不再渲染
    expect(screen.queryByLabelText('搜索账单')).not.toBeInTheDocument()
    await user.click(screen.getByTestId('open-search'))
    expect(mockNavigate).toHaveBeenCalledWith('/search', { state: { bookId: 'b1' } })
  })

  it('opens year/month picker and switches to previous month', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('午饭')
    await user.click(screen.getByLabelText('切换年份和月份'))
    const panel = await screen.findByText('1月').then(el => el.closest('div.grid')!)
    await user.click(within(panel as HTMLElement).getByText(`${prevDate.getMonth() + 1}月`))
    await waitFor(() => expect(screen.getByText(prevLabel)).toBeInTheDocument())
    expect(screen.getByText('八月工资')).toBeInTheDocument()
    expect(screen.queryByText('午饭')).not.toBeInTheDocument()
  })

  it('switches year backwards and shows empty state for a year without records', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('午饭')
    await user.click(screen.getByLabelText('切换年份和月份'))
    await user.click(screen.getByLabelText('上一年'))
    const yearSpan = await screen.findByText(`${NOW.getFullYear() - 1}年`)
    expect(yearSpan).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '1月' }))
    await waitFor(() =>
      expect(within(screen.getByTestId('empty-state')).getByText('本月暂无记录')).toBeInTheDocument()
    )
  })

  it('deletes via wrapper with a UUID mutationId', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderPage()
    await screen.findByText('午饭')
    await user.click(screen.getAllByLabelText('删除')[0])
    await waitFor(() => expect(mockBridge.softDeleteTransaction).toHaveBeenCalledTimes(1))
    const [id, mutationId] = mockBridge.softDeleteTransaction.mock.calls[0]
    expect(id).toBe('tx-cur')
    expect(mutationId).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    // T12-10：行内删除钮与行点击开层共存——点 ✕ 不开编辑层
    expect(screen.queryByTestId('tx-edit-sheet')).toBeNull()
    vi.restoreAllMocks()
  })

  it('点击流水行打开 TxEditSheet，金额预填自行数据（T12-10 / REQ-36）', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('午饭')
    await user.click(screen.getByText('午饭'))
    const sheet = await screen.findByTestId('tx-edit-sheet')
    expect(within(sheet).getByTestId('tx-edit-amount')).toHaveValue(55)
    expect(within(sheet).getByTestId('tx-edit-note')).toHaveValue('午饭')
  })
})

/**
 * 第七轮 REQ-4 / T7-4：明细页标题改为账本切换入口 + 总账本聚合视图。
 */
describe('DetailPage 账本切换（REQ-4）', () => {
  const mkBookTx = (bookId: string, id: string, note: string): TransactionData =>
    mkTx({ id, bookId, note, bizDate: `${curPeriod}-03`, categoryId: 'cat-food', amountCents: 3000 })

  // 模拟 BookDao.listActive 的 ORDER BY name：总账本(U+603B) 排在账本A/账本B 之前（决策 #2 坑）
  const BOOKS = [
    { id: 'm1', name: '总账本', icon: '📊', isMaster: true },
    { id: 'a', name: '账本A', icon: '🅰️' },
    { id: 'b', name: '账本B', icon: '🅱️' },
  ]
  const TX_A = mkBookTx('a', 'tx-a', 'A的午饭')
  const TX_B = mkBookTx('b', 'tx-b', 'B的打车')

  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.call.mockImplementation((cmd: string, payload?: { bookId?: string }) => {
      if (cmd === 'book.list') return Promise.resolve(BOOKS)
      if (cmd === 'category.list') return Promise.resolve(CATS)
      if (cmd === 'transaction.listByBook') {
        if (payload?.bookId === 'a') return Promise.resolve([TX_A])
        if (payload?.bookId === 'b') return Promise.resolve([TX_B])
        // master 自身不存流水：若被查询说明实现走错分支
        if (payload?.bookId === 'm1') return Promise.resolve([{ ...TX_A, id: 'LEAK', note: 'LEAKED' }])
        return Promise.resolve([])
      }
      return Promise.resolve([])
    })
  })

  it('默认选中第一个非 master 账本并只显示其流水', async () => {
    renderPage()
    await screen.findByLabelText('切换账本')
    // 标题位显示"账本A"（非 master），而非"总账本"
    expect(screen.getByLabelText('切换账本')).toHaveTextContent('账本A')
    await waitFor(() => expect(screen.getByText('A的午饭')).toBeInTheDocument())
    expect(screen.queryByText('B的打车')).not.toBeInTheDocument()
    expect(screen.queryByText('LEAKED')).not.toBeInTheDocument()
    // 从不查询 master 的流水
    const queried = mockBridge.call.mock.calls
      .filter(c => c[0] === 'transaction.listByBook')
      .map(c => c[1]?.bookId)
    expect(queried).toContain('a')
    expect(queried).not.toContain('m1')
  })

  it('切到总账本后合并展示所有普通账本流水', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByLabelText('切换账本')
    await user.click(screen.getByLabelText('切换账本'))
    // 面板内 master 行带"汇总"徽标
    const masterBtn = await screen.findByRole('button', { name: /总账本/ })
    expect(masterBtn).toHaveTextContent('汇总')
    await user.click(masterBtn)
    // 合并 a + b 两账本流水，且未查询 m1
    await waitFor(() => expect(screen.getByText('A的午饭')).toBeInTheDocument())
    expect(screen.getByText('B的打车')).toBeInTheDocument()
    expect(screen.queryByText('LEAKED')).not.toBeInTheDocument()
    const queried = mockBridge.call.mock.calls
      .filter(c => c[0] === 'transaction.listByBook')
      .map(c => c[1]?.bookId)
    expect(queried).toContain('a')
    expect(queried).toContain('b')
    expect(queried).not.toContain('m1')
  })

  it('切到账本B后只显示B的记录', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByLabelText('切换账本')
    await user.click(screen.getByLabelText('切换账本'))
    await user.click(await screen.findByRole('button', { name: /账本B/ }))
    await waitFor(() => expect(screen.getByText('B的打车')).toBeInTheDocument())
    expect(screen.queryByText('A的午饭')).not.toBeInTheDocument()
    expect(screen.getByLabelText('切换账本')).toHaveTextContent('账本B')
    // T12-5：圆钮携带切换后的账本 id
    await user.click(screen.getByTestId('open-search'))
    expect(mockNavigate).toHaveBeenCalledWith('/search', { state: { bookId: 'b' } })
  })
})

// ─── T18-2 / REQ-67①：同步完成 → 静默重取（不闪加载态） ───

describe('DetailPage 同步重取（T18-2 / REQ-67①）', () => {
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

  it('同步轮完成 → 重取；刷新在途不置 loading（不闪骨架）', async () => {
    // 页面挂载前已同步过（非零基准），随后的同步轮前进即触发重取
    useSyncStore.setState(s => ({ status: { ...s.status, lastSyncedAt: 1000 } }))
    let rows = [mkTx({ id: 'tx-cur', bizDate: `${curPeriod}-03`, note: '午饭', amountCents: 5500 })]
    let releaseSecond: ((v: TransactionData[]) => void) | null = null
    let callCount = 0
    mockBridge.call.mockImplementation((cmd: string) => {
      if (cmd === 'book.list') return Promise.resolve([{ id: 'b1' }])
      if (cmd === 'category.list') return Promise.resolve(CATS)
      if (cmd === 'transaction.listByBook') {
        callCount += 1
        // 第二次（同步后的重取）挂起，便于观察刷新在途时的 UI
        if (callCount === 2) {
          return new Promise(resolve => { releaseSecond = resolve as (v: TransactionData[]) => void })
        }
        return Promise.resolve(rows)
      }
      return Promise.resolve([])
    })

    const { container } = renderPage()
    await waitFor(() => expect(screen.getByText('午饭')).toBeInTheDocument())
    expect(container.querySelector('.animate-spin')).toBeNull()

    // 服务端权威行变了 → 一轮同步完成把新值拉回本地
    rows = [mkTx({ id: 'tx-cur', bizDate: `${curPeriod}-03`, note: '午饭', amountCents: 6600 })]
    emitSynced(2000) // 前进 → 重取

    // 重取已发起（挂起中）：静默路径不得回到 loading
    await waitFor(() => expect(releaseSecond).not.toBeNull())
    expect(container.querySelector('.animate-spin')).toBeNull()
    expect(screen.getByText('午饭')).toBeInTheDocument()

    act(() => { releaseSecond!(rows) })
    await waitFor(() => expect(screen.getByText('66')).toBeInTheDocument())
  })
})
