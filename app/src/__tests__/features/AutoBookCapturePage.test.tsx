/**
 * AutoBookCapturePage 测试 — T21-8 / REQ-83。
 *
 * 覆盖：
 *  - 列表渲染（pending_confirm 条目 → 卡片）
 *  - 多来源徽标（L2 合并：`multiSource` + `sourceCount`）
 *  - 疑似重复提示（`parsedJson.hint === 'possible_duplicate'`）
 *  - 低置信度徽标（`confidence < 0.7`）
 *  - 处置：确认 / 改后确认 / 忽略
 *  - 已入账（committed）/ 已忽略（ignored）不再显示操作按钮
 *  - 空态
 *  - 排序：pending_confirm 在前，committed 在后
 *  - 自动记账未启用时的顶部提示
 *
 * 不覆盖：
 *  - 真机投递（决策 #19，归 T21-9 收口）
 *  - 服务端解析（T21-2 / T21-6 各自用例）
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { AutoBookCapturePage } from '../../features/autobook/AutoBookCapturePage'
import type {
  AutoBookCaptureData,
  AutoBookStatusData,
  CategoryData,
} from '../../platform/bridge/bridge-types'

const mockBridge = vi.hoisted(() => ({
  call: vi.fn(),
  getMeta: vi.fn().mockResolvedValue({}),
  isAvailable: () => true,
}))
vi.mock('../../platform/hooks/use-bridge', () => ({ useBridge: () => mockBridge }))
vi.mock('../../components/SyncBadge', () => ({ SyncBadge: () => <span /> }))

/** 构造一条 capture 测试数据 */
const mkCapture = (
  over: Partial<AutoBookCaptureData> & { captureId: string },
): AutoBookCaptureData => ({
  sourceChannel: 'notification',
  sourcePackage: 'com.tencent.mm',
  sourceApp: '微信',
  rawText: '微信支付：消费 32.00 元',
  postedAt: new Date('2026-10-14T14:32:00+08:00').getTime(),
  merchantKey: '星巴克',
  status: 'pending_confirm',
  parsedJson: {
    kind: 'transaction',
    type: 'expense',
    amountCents: 3200,
    category: '餐饮',
    note: '',
    bizDate: '2026-10-14',
    confidence: 0.9,
  },
  confidence: 0.9,
  transactionId: null,
  sourceCount: 1,
  multiSource: false,
  ...over,
})

const CATEGORIES: CategoryData[] = [
  { id: 'c1', kind: 'expense', name: '餐饮', parentId: null, icon: '🍔', version: 1 },
  { id: 'c2', kind: 'expense', name: '交通', parentId: null, icon: '🚇', version: 1 },
  { id: 'c3', kind: 'income', name: '工资', parentId: null, icon: '💰', version: 1 },
]

const DEFAULT_STATUS: AutoBookStatusData = {
  enabled: true,
  accessibilityStatus: 'enabled',
  notificationStatus: 'enabled',
  accessibilityGranted: true,
  notificationGranted: true,
  todayCaptureCount: 3,
  pendingConfirmCount: 1,
}

function setup(opts: {
  captures?: AutoBookCaptureData[]
  status?: Partial<AutoBookStatusData>
  categories?: CategoryData[]
} = {}) {
  const status = { ...DEFAULT_STATUS, ...opts.status }
  mockBridge.call.mockImplementation((cmd: string) => {
    if (cmd === 'autobook.get_status') return Promise.resolve(status)
    if (cmd === 'autobook.list_captures') return Promise.resolve(opts.captures ?? [])
    if (cmd === 'category.list') return Promise.resolve(opts.categories ?? CATEGORIES)
    if (cmd === 'autobook.resolve_capture') return Promise.resolve({ success: true })
    return Promise.resolve([])
  })
}

function renderPage() {
  return render(
    <MemoryRouter>
      <AutoBookCapturePage />
    </MemoryRouter>,
  )
}

describe('AutoBookCapturePage — T21-8 / REQ-83', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('空列表 → 空态「没有需要确认的支付」', async () => {
    setup({ captures: [] })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('capture-empty')).toBeInTheDocument())
    expect(screen.getByText(/没有需要确认的支付/)).toBeInTheDocument()
  })

  it('pending_confirm 条目 → 渲染卡片（商户名 / 金额 / 来源 / 时间）', async () => {
    setup({
      captures: [mkCapture({ captureId: 'cap1', merchantKey: '星巴克' })],
    })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('capture-card-cap1')).toBeInTheDocument())
    // 标题 = parsedJson.category（'餐饮'）优先于 merchantKey（决策：分类名优先）
    expect(screen.getByText('餐饮')).toBeInTheDocument()
    // 金额：-¥32.00（符号与金额分两个 span 渲染）
    expect(screen.getByText(/32\.00/)).toBeInTheDocument()
    expect(screen.getByText(/微信 · 14:32/)).toBeInTheDocument()
    // 三按钮都在
    expect(screen.getByTestId('capture-confirm-cap1')).toBeInTheDocument()
    expect(screen.getByTestId('capture-edit-cap1')).toBeInTheDocument()
    expect(screen.getByTestId('capture-ignore-cap1')).toBeInTheDocument()
  })

  it('parsedJson 无分类时 → 标题回落到 merchantKey', async () => {
    setup({
      captures: [mkCapture({
        captureId: 'cap1fb',
        merchantKey: '星巴克',
        parsedJson: { kind: 'transaction', type: 'expense', amountCents: 3200, confidence: 0.9 },
      })],
    })
    renderPage()
    await waitFor(() => expect(screen.getByText('星巴克')).toBeInTheDocument())
  })

  it('多来源合并卡 → 显示「已由 N 个来源确认」徽标', async () => {
    setup({
      captures: [mkCapture({ captureId: 'cap2', multiSource: true, sourceCount: 2 })],
    })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('capture-multi-source-cap2')).toBeInTheDocument())
    expect(screen.getByText(/已由 2 个来源确认/)).toBeInTheDocument()
  })

  it('疑似重复（L3）→ 显示提示徽标（不预选合并，也不预选删除）', async () => {
    setup({
      captures: [mkCapture({
        captureId: 'cap3',
        parsedJson: {
          kind: 'transaction',
          type: 'expense',
          amountCents: 1800,
          category: '餐饮',
          hint: 'possible_duplicate',
          confidence: 0.85,
        },
      })],
    })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('capture-duplicate-hint-cap3')).toBeInTheDocument())
    expect(screen.getByText(/可能与另一笔同额账目重复/)).toBeInTheDocument()
  })

  it('低置信度（< 0.7）→ 显示「AI 拿不太准」徽标', async () => {
    setup({
      captures: [mkCapture({ captureId: 'cap4', confidence: 0.5 })],
    })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('capture-low-confidence')).toBeInTheDocument())
  })

  it('高置信度（≥ 0.7）→ 不显示低置信徽标', async () => {
    setup({
      captures: [mkCapture({ captureId: 'cap5', confidence: 0.95 })],
    })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('capture-card-cap5')).toBeInTheDocument())
    expect(screen.queryByTestId('capture-low-confidence')).toBeNull()
  })

  it('点击「✓ 确认」→ 调用 resolve_capture confirm 后重取', async () => {
    setup({ captures: [mkCapture({ captureId: 'cap6' })] })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('capture-card-cap6')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('capture-confirm-cap6'))
    await waitFor(() => {
      const resolveCalls = mockBridge.call.mock.calls.filter(c => c[0] === 'autobook.resolve_capture')
      expect(resolveCalls.length).toBeGreaterThanOrEqual(1)
      const payload = resolveCalls[0][1]
      expect(payload.captureId).toBe('cap6')
      expect(payload.resolution).toBe('confirm')
    })
  })

  it('点击「× 忽略」→ 调用 resolve_capture ignore', async () => {
    setup({ captures: [mkCapture({ captureId: 'cap7' })] })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('capture-card-cap7')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('capture-ignore-cap7'))
    await waitFor(() => {
      const resolveCalls = mockBridge.call.mock.calls.filter(c => c[0] === 'autobook.resolve_capture')
      const payload = resolveCalls[0][1]
      expect(payload.resolution).toBe('ignore')
    })
  })

  it('点击「✎ 修改」→ 展开编辑面板（收支 / 金额 / 分类 / 备注 / 日期）', async () => {
    setup({ captures: [mkCapture({ captureId: 'cap8' })] })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('capture-card-cap8')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('capture-edit-cap8'))
    await waitFor(() => expect(screen.getByTestId('capture-edit-panel-cap8')).toBeInTheDocument())
    // 编辑面板内字段齐备
    expect(screen.getByTestId('capture-edit-type-expense')).toBeInTheDocument()
    expect(screen.getByTestId('capture-edit-type-income')).toBeInTheDocument()
    expect(screen.getByTestId('capture-edit-amount-cap8')).toBeInTheDocument()
    expect(screen.getByTestId('capture-edit-category-cap8')).toBeInTheDocument()
    expect(screen.getByTestId('capture-edit-note-cap8')).toBeInTheDocument()
    expect(screen.getByTestId('capture-edit-date-cap8')).toBeInTheDocument()
    expect(screen.getByTestId('capture-edit-save-cap8')).toBeInTheDocument()
  })

  it('已 committed 状态 → 不显示操作按钮，只显「已入账」', async () => {
    setup({
      captures: [mkCapture({ captureId: 'cap9', status: 'committed' })],
    })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('capture-resolved-cap9')).toBeInTheDocument())
    expect(screen.getByText(/已入账/)).toBeInTheDocument()
    expect(screen.queryByTestId('capture-confirm-cap9')).toBeNull()
    expect(screen.queryByTestId('capture-ignore-cap9')).toBeNull()
  })

  it('已 ignored 状态 → 显示「已忽略」', async () => {
    setup({
      captures: [mkCapture({ captureId: 'cap10', status: 'ignored' })],
    })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('capture-resolved-cap10')).toBeInTheDocument())
    expect(screen.getByText(/已忽略/)).toBeInTheDocument()
  })

  it('排序：pending_confirm 在前，committed 在后', async () => {
    const committed = mkCapture({ captureId: 'old', status: 'committed', postedAt: 1000 })
    const pending = mkCapture({ captureId: 'new', status: 'pending_confirm', postedAt: 2000 })
    setup({ captures: [committed, pending] })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('capture-card-new')).toBeInTheDocument())
    // DOM 顺序：pending 在前
    const cards = screen.getAllByTestId(/capture-card-/)
    expect(cards[0].getAttribute('data-testid')).toBe('capture-card-new')
    expect(cards[1].getAttribute('data-testid')).toBe('capture-card-old')
  })

  it('自动记账未启用 → 顶部给启用提示', async () => {
    setup({
      captures: [mkCapture({ captureId: 'cap11' })],
      status: { enabled: false },
    })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('capture-disabled-hint')).toBeInTheDocument())
    expect(screen.getByText(/自动记账未开启/)).toBeInTheDocument()
    // 但历史待确认仍渲染
    expect(screen.getByTestId('capture-card-cap11')).toBeInTheDocument()
  })

  it('顶部小结正确反映待确认计数（排除已 committed/ignored）', async () => {
    setup({
      captures: [
        mkCapture({ captureId: 'p1', status: 'pending_confirm' }),
        mkCapture({ captureId: 'p2', status: 'pending_confirm' }),
        mkCapture({ captureId: 'c1', status: 'committed' }),
        mkCapture({ captureId: 'i1', status: 'ignored' }),
      ],
    })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('capture-summary')).toBeInTheDocument())
    // 共 4 条，2 条待确认
    expect(screen.getByText(/共 4 条，2 条待确认/)).toBeInTheDocument()
  })
})
